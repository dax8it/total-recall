"""Install pinned Total Recall source trees for Hermes' local-path dependency."""
from __future__ import annotations

import ast
import os
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .hermes_installer import _looks_like_total_recall_plugin, validate_plugin_bundle

REPOSITORY = "https://github.com/dax8it/total-recall.git"
RELEASE_DIR = "total-recall-releases"
PLUGIN = Path("hermes-plugin/total-recall")
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _git(root: Path, *args: str, required: bool = True) -> str | None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        if required:
            raise ValueError(result.stderr.strip() or f"git {args[0]} exited {result.returncode}")
        return None
    return result.stdout.rstrip("\n")


def source_provenance(plugin: Path) -> dict:
    """Identify the resolved plugin tree, not the checkout running doctor."""
    resolved = plugin.resolve()
    root = resolved.parents[1]
    if resolved != root / PLUGIN or not (root / ".git").exists():
        return {"kind": "untracked_copy", "path": str(resolved),
                "message": "Plugin copy has no verified release tag."}
    commit = _git(root, "rev-parse", "--verify", "HEAD", required=False)
    tags = _git(root, "tag", "--points-at", "HEAD", required=False)
    status = _git(root, "status", "--porcelain", "--untracked-files=all", required=False)
    if commit is None or tags is None or status is None:
        return {"kind": "unknown", "path": str(resolved), "message": "Could not verify the plugin Git source."}
    exact = sorted(tag for tag in tags.splitlines() if TAG.fullmatch(tag))
    managed = root.parent.name == RELEASE_DIR and TAG.fullmatch(root.name) is not None
    tag = root.name if managed and root.name in exact else next(iter(exact), None)
    detached = _git(root, "branch", "--show-current") == ""
    if managed:
        if tag != root.name or not detached:
            kind = "untagged_commit" if not exact else "invalid_release"
            message = (f"Release copy is at untagged commit {commit}." if not exact else
                       f"Release copy is not detached at its expected tag {root.name}.")
        elif status:
            kind, message = "modified_release", f"Release copy {tag} has local changes."
        else:
            kind, message = "release", f"Tagged release {tag} ({commit})."
    else:
        kind = "development_checkout"
        identity = f"at tag {tag}" if tag else f"at untagged commit {commit}"
        message = f"Development checkout {identity}. Not a managed release."
    if status and kind != "modified_release":
        message += " The source has local changes."
    return {"kind": kind, "root": str(root), "path": str(resolved), "commit": commit,
            "tag": tag, "dirty": bool(status), "message": message}


def _validate_release(root: Path, tag: str) -> dict:
    provenance = source_provenance(root / PLUGIN)
    if provenance["kind"] != "release" or provenance["tag"] != tag:
        raise ValueError(provenance["message"])
    _validate_contents(root, tag)
    return provenance


def _validate_contents(root: Path, tag: str) -> None:
    plugin = root / PLUGIN
    if not validate_plugin_bundle(plugin, strict_clean=False)["ok"]:
        raise ValueError(f"{tag} does not contain a complete Hermes plugin.")
    version = tag[1:]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if not (plugin / "pyproject.toml").is_file():
        raise ValueError(f"{tag} predates the Hermes release layout. Use v1.4.0 or newer.")
    declaration = tomllib.loads((plugin / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = (plugin / "plugin.yaml").read_text(encoding="utf-8")
    if (project.get("version") != version or declaration["project"].get("version") != version
            or f"total-recall-core=={version}" not in declaration["project"].get("dependencies", [])
            or declaration["tool"]["uv"]["sources"].get("total-recall-core") != {"path": "../.."}
            or f"version: {version}" not in manifest.splitlines()
            or not (root / "scripts/hermes_capture_watch.py").is_file()):
        raise ValueError(f"{tag} does not match the Total Recall release layout and version.")


def _fetch_release(releases: Path, tag: str, source: str) -> Path:
    target = releases / tag
    if target.is_symlink():
        raise ValueError(f"Release directory must not be a symlink: {target}")
    if target.exists():
        return target
    ref = f"refs/tags/{tag}"
    with tempfile.TemporaryDirectory(prefix=".fetch-", dir=releases) as temporary:
        staged = Path(temporary) / "source"
        staged.mkdir()
        _git(staged, "init", "--quiet", "--template=")
        _git(staged, "fetch", "--quiet", "--depth=1", "--no-tags", "--no-recurse-submodules",
             "--", source, f"{ref}:{ref}")
        _git(staged, "checkout", "--quiet", "--detach", f"{ref}^{{commit}}")
        _validate_contents(staged, tag)
        staged.rename(target)
    return target


def _replace_link(path: Path, target: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".total-recall-", dir=path.parent) as directory:
        new = Path(directory) / "link"
        new.symlink_to(target, target_is_directory=True)
        new.replace(path)


def _sync_hermes(hermes_bin: str, home: Path) -> tuple[list[str], subprocess.CompletedProcess]:
    env = {key: value for key, value in os.environ.items()
           if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "HERMES_GATEWAY_PROFILE_PIN"}}
    env["HERMES_HOME"] = str(home)
    probe = [hermes_bin, "--run-module", "site"]
    options = {"env": env, "capture_output": True, "text": True}
    launch = subprocess.run(probe, **options, timeout=120)
    if launch.returncode and "no dependency environment is committed for this install" in launch.stderr:
        # A fresh Hermes root needs the baseline before the launcher can run a module.
        baseline = subprocess.run([hermes_bin, "pm", "repair"], **options)
        if baseline.returncode:
            raise ValueError(baseline.stderr.strip() or baseline.stdout.strip() or f"Hermes exited {baseline.returncode}.")
        launch = subprocess.run(probe, **options, timeout=120)
    if launch.returncode:
        raise ValueError(launch.stderr.strip() or launch.stdout.strip() or f"Hermes exited {launch.returncode}.")
    try:
        paths = ast.literal_eval(launch.stdout.split("sys.path = ", 1)[1].split("\nUSER_BASE:", 1)[0])
        root = Path(paths[0])
        if not root.is_absolute() or not (root / "pm/client.py").is_file():
            raise ValueError("The launcher did not report a Hermes PM source path.")
        venv = next(parent for entry in paths if Path(entry).name == "site-packages"
                    for parent in Path(entry).parents if (parent / "pyvenv.cfg").is_file())
    except (IndexError, SyntaxError, TypeError, ValueError, StopIteration) as exc:
        raise ValueError("Could not locate managed Hermes from `hermes --run-module site`.") from exc
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # Repair replays the old dependency graph. Sync rediscovers the selected
    # provider paths without adding extras or rewriting profile configuration.
    command = [str(python), "-IB", "-c",
               "import sys; sys.path.insert(0, sys.argv[1]); from pm.client import sync_venv; sync_venv(explicit=True)",
               str(root)]
    return command, subprocess.run(command, **options)


def install_release(*, tag: str, hermes_home: str, source: str, profiles: list[str],
                    migrate: bool, rebuild: bool, hermes_bin: str, dry_run: bool) -> dict:
    home = Path(hermes_home or Path.home() / ".hermes").expanduser().resolve()
    releases = home / RELEASE_DIR
    current = releases / "current"
    canonical = home / "plugins/memory/total-recall"
    alias = home / "plugins/total-recall"
    result = {"ok": False, "tag": tag, "path": str(canonical), "dryRun": dry_run,
              "releasePath": str(releases / tag), "current": str(current)}
    try:
        if not TAG.fullmatch(tag):
            raise ValueError("Pass an exact release tag such as v1.4.1, not a branch or commit.")
        if any(not PROFILE.fullmatch(name) or name == "default" or not (home / "profiles" / name / "config.yaml").is_file()
               for name in profiles):
            raise ValueError("--link-profile requires an existing named Hermes profile.")
        if releases.is_symlink() or (current.exists() and not current.is_symlink()):
            raise ValueError(f"Not a managed release directory or pointer: {releases}")
        previous = Path(os.readlink(current)) if current.is_symlink() else None
        if previous is not None and (previous.parent != Path(".") or not TAG.fullmatch(previous.name)):
            raise ValueError(f"Not a managed release pointer: {current}")
        links = {canonical: current / PLUGIN, alias: canonical}
        for name in dict.fromkeys(profiles):
            base = home / "profiles" / name / "plugins"
            links[base / "memory/total-recall"] = canonical
            links[base / "total-recall"] = base / "memory/total-recall"
        pending = [(path, target) for path, target in links.items() if not (
            path.is_symlink() and Path(os.path.abspath(path.parent / os.readlink(path))) == target
        )]
        for path, _ in pending:
            if not path.parent.resolve().is_relative_to(home):
                raise ValueError(f"Plugin directory resolves outside the Hermes home: {path.parent}")
            if path.exists() or path.is_symlink():
                if not _looks_like_total_recall_plugin(path):
                    raise ValueError(f"Refusing to replace a foreign or broken plugin at {path}")
                if not migrate:
                    raise ValueError(f"Existing plugin at {path}. Use --migrate to preserve it as a backup and replace it.")
        result["links"] = {str(path): str(target) for path, target in links.items()}
        result["backupPaths"] = [str(path) for path, _ in pending if path.exists() or path.is_symlink()]
        if dry_run:
            return {**result, "ok": True, "message": f"Would select release {tag}. No changes made.",
                    "rebuildRequested": rebuild}
        releases.mkdir(parents=True, exist_ok=True)
        target = _fetch_release(releases, tag, source)
        result["source"] = _validate_release(target, tag)
        backup = None
        changed = []
        try:
            for path, destination in pending:
                saved = None
                if path.exists() or path.is_symlink():
                    if backup is None:
                        backup = Path(tempfile.mkdtemp(prefix="migration-", dir=releases))
                        result["backup"] = str(backup)
                    saved = backup / path.relative_to(home)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    if path.is_symlink():
                        saved.symlink_to(os.readlink(path))
                    else:
                        path.rename(saved)
                changed.append((path, saved))
                _replace_link(path, destination)
            _replace_link(current, Path(tag))
        except OSError:
            for path, saved in reversed(changed):
                if saved is None:
                    path.unlink(missing_ok=True)
                elif saved.is_symlink():
                    _replace_link(path, Path(os.readlink(saved)))
                else:
                    saved.replace(path)
            raise
        result["selected"] = True
        if rebuild:
            try:
                command, built = _sync_hermes(hermes_bin, home)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                raise ValueError(f"Selected {tag}, but could not start Hermes PM sync. Do not restart Hermes. {exc}") from exc
            result["rebuild"] = {"command": command, "returncode": built.returncode,
                                 "stdout": built.stdout.strip(), "stderr": built.stderr.strip()}
            if built.returncode:
                raise ValueError(f"Selected {tag}, but Hermes PM sync failed. Do not restart Hermes. "
                                 f"{built.stderr.strip() or built.stdout.strip() or f'Exit {built.returncode}.'}")
        return {**result, "ok": True, "message": f"Selected {tag}. Restart running Hermes processes separately."}
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {**result, "error": str(exc)}


def runtime_source_root(core: dict) -> Path | None:
    url = urlsplit(core.get("checkoutUrl") or "")
    if url.scheme == "file" and url.netloc in {"", "localhost"}:
        return Path(url2pathname(url.path)).resolve()
    return None
