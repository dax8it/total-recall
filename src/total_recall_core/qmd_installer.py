from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List

QMD_BINARY = "qmd"
_SYSTEM_DIRS = {
    "/bin",
    "/sbin",
    "/usr/bin",
    "/usr/sbin",
    "/System/Cryptexes/App/usr/bin",
}


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser()


def _is_executable_file(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _path_entries(path_env: str | None = None) -> List[Path]:
    raw = path_env if path_env is not None else os.environ.get("PATH", "")
    entries: List[Path] = []
    for item in raw.split(os.pathsep):
        if not item:
            continue
        path = _expand(item)
        if path not in entries:
            entries.append(path)
    return entries


def _candidate_source_paths(explicit_source: str = "") -> List[Path]:
    candidates: List[Path] = []
    if explicit_source:
        candidates.append(_expand(explicit_source))
    env_path = os.environ.get("TOTAL_RECALL_QMD_BIN", "")
    if env_path:
        candidates.append(_expand(env_path))
    which = shutil.which(QMD_BINARY)
    if which:
        candidates.append(_expand(which))
    home = Path.home()
    candidates.extend(
        [
            home / ".bun" / "bin" / QMD_BINARY,
            home / ".npm-global" / "bin" / QMD_BINARY,
            home / ".local" / "bin" / QMD_BINARY,
            Path("/opt/homebrew/bin") / QMD_BINARY,
            Path("/usr/local/bin") / QMD_BINARY,
        ]
    )
    unique: List[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def resolve_qmd_source(explicit_source: str = "") -> Dict[str, Any]:
    checked = []
    for candidate in _candidate_source_paths(explicit_source):
        checked.append(str(candidate))
        if _is_executable_file(candidate):
            return {
                "ok": True,
                "status": "FOUND",
                "path": str(candidate),
                "realPath": str(candidate.resolve()),
                "checked": checked,
            }
    return {
        "ok": False,
        "status": "QMD_NOT_FOUND",
        "checked": checked,
        "message": "qmd was not found. Install it first with `npm install -g @tobilu/qmd` or `bun install -g @tobilu/qmd`.",
    }


def _writable_existing_dir(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.W_OK | os.X_OK)
    except OSError:
        return False


def _is_user_dir(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(Path.home().resolve())
    except Exception:
        return False


def _safe_writable_path_dirs() -> List[Path]:
    writable = []
    for entry in _path_entries():
        entry_str = str(entry)
        if entry_str in _SYSTEM_DIRS:
            continue
        if _writable_existing_dir(entry) and entry not in writable:
            writable.append(entry)
    writable.sort(key=lambda p: (0 if _is_user_dir(p) else 1, str(p)))
    return writable


def choose_qmd_link_dir(*, bin_dir: str = "") -> Dict[str, Any]:
    if bin_dir:
        path = _expand(bin_dir)
        if path.exists() and not path.is_dir():
            return {"ok": False, "status": "BIN_DIR_NOT_DIRECTORY", "path": str(path)}
        return {
            "ok": True,
            "status": "EXPLICIT",
            "path": str(path),
            "onPath": path in _path_entries(),
        }

    for entry in _safe_writable_path_dirs():
        return {"ok": True, "status": "WRITABLE_PATH_ENTRY", "path": str(entry), "onPath": True}

    fallback = Path.home() / ".local" / "bin"
    return {
        "ok": True,
        "status": "USER_FALLBACK_NOT_ON_PATH" if fallback not in _path_entries() else "USER_FALLBACK",
        "path": str(fallback),
        "onPath": fallback in _path_entries(),
        "message": "No writable PATH directory was found; using ~/.local/bin. Add it to PATH if your shell does not already include it.",
    }


def _same_target(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def link_qmd(*, source: str = "", bin_dir: str = "", force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    resolved = resolve_qmd_source(source)
    link_dir = choose_qmd_link_dir(bin_dir=bin_dir)
    payload: Dict[str, Any] = {
        "ok": False,
        "binary": QMD_BINARY,
        "source": resolved,
        "linkDir": link_dir,
        "dryRun": dry_run,
    }
    if not resolved.get("ok"):
        payload.update({"status": resolved.get("status"), "message": resolved.get("message")})
        return payload
    if not link_dir.get("ok"):
        payload.update({"status": link_dir.get("status"), "message": "No installable qmd link directory could be selected."})
        return payload

    source_path = Path(str(resolved["path"])).expanduser()
    destination = Path(str(link_dir["path"])).expanduser() / QMD_BINARY
    payload["destination"] = str(destination)

    if destination.exists() or destination.is_symlink():
        if _same_target(destination, source_path):
            payload.update(
                {
                    "ok": True,
                    "status": "ALREADY_LINKED",
                    "message": "qmd already resolves to the selected source.",
                    "pathHint": _path_hint(destination.parent, bool(link_dir.get("onPath"))),
                }
            )
            return payload
        if not force:
            payload.update(
                {
                    "ok": False,
                    "status": "DESTINATION_EXISTS",
                    "message": f"{destination} already exists and does not resolve to {source_path}. Pass --force to replace a qmd file/symlink you control.",
                }
            )
            return payload
        if destination.is_dir() and not destination.is_symlink():
            payload.update({"ok": False, "status": "DESTINATION_IS_DIRECTORY", "message": f"Refusing to replace directory {destination}."})
            return payload

    if dry_run:
        payload.update(
            {
                "ok": True,
                "status": "DRY_RUN",
                "message": f"Would link {destination} -> {source_path}.",
                "pathHint": _path_hint(destination.parent, bool(link_dir.get("onPath"))),
            }
        )
        return payload

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    destination.symlink_to(source_path)
    probe = _probe_qmd(destination)
    ok = bool(probe.get("ok"))
    payload.update(
        {
            "ok": ok,
            "status": "LINKED" if ok else "LINKED_BUT_PROBE_FAILED",
            "message": f"Linked {destination} -> {source_path}.",
            "probe": probe,
            "pathHint": _path_hint(destination.parent, bool(link_dir.get("onPath"))),
        }
    )
    return payload


def qmd_link_status(*, source: str = "", bin_dir: str = "") -> Dict[str, Any]:
    resolved = resolve_qmd_source(source)
    link_dir = choose_qmd_link_dir(bin_dir=bin_dir)
    destination = Path(str(link_dir.get("path") or Path.home() / ".local" / "bin")).expanduser() / QMD_BINARY
    linked = bool(destination.exists() or destination.is_symlink())
    return {
        "ok": bool(resolved.get("ok")),
        "binary": QMD_BINARY,
        "source": resolved,
        "linkDir": link_dir,
        "destination": str(destination),
        "destinationExists": linked,
        "destinationResolvesToSource": bool(linked and resolved.get("ok") and _same_target(destination, Path(str(resolved["path"])))),
        "pathHint": _path_hint(destination.parent, bool(link_dir.get("onPath"))),
    }


def _probe_qmd(path: Path) -> Dict[str, Any]:
    attempts = []
    for args in (["status"], ["--help"]):
        try:
            completed = subprocess.run([str(path), *args], text=True, capture_output=True, check=False, timeout=15)
        except Exception as exc:
            attempts.append({"args": args, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        attempt = {
            "args": args,
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
        attempts.append(attempt)
        if completed.returncode == 0:
            return {**attempt, "attempts": attempts}
    return {"ok": False, "attempts": attempts}


def _path_hint(bin_dir: Path, on_path: bool) -> str:
    if on_path:
        return f"{bin_dir} is already on PATH."
    return f"Add {bin_dir} to PATH, e.g. `export PATH=\"{bin_dir}:$PATH\"`."
