from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict

from . import __version__

PLUGIN_NAME = "total-recall"
DEFAULT_CONTEXT_RISK_THRESHOLD = "0.55"

PLUGIN_INIT = '''from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_total_recall_core() -> None:
    """Allow checkout installs while keeping the distributable plugin tiny."""
    candidates = []
    env_path = os.getenv("TOTAL_RECALL_CORE_SRC")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "src")
    for candidate in candidates:
        if (candidate / "total_recall_core").is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return


_bootstrap_total_recall_core()

from total_recall_core.hermes_provider import (  # noqa: E402,F401
    ALL_SCHEMAS,
    CHECKPOINT_SCHEMA,
    FEDERATION_QUERY_SCHEMA,
    HANDOFF_EXPORT_SCHEMA,
    INCIDENTS_SCHEMA,
    KNOWLEDGE_COMPILED_TRUTH_SCHEMA,
    KNOWLEDGE_FRESHNESS_SCHEMA,
    KNOWLEDGE_GRAPH_INSPECT_SCHEMA,
    KNOWLEDGE_GRAPH_TIMELINE_SCHEMA,
    KNOWLEDGE_QUERY_SCHEMA,
    KNOWLEDGE_STATUS_SCHEMA,
    KNOWLEDGE_SYNTHESIS_STATUS_SCHEMA,
    LEARNING_REVIEW_SCHEMA,
    LOOP_COMPLETE_SCHEMA,
    LOOP_INBOX_SCHEMA,
    LOOP_NOTE_SCHEMA,
    LOOP_START_SCHEMA,
    LOOP_VERIFY_SCHEMA,
    REHYDRATE_SCHEMA,
    SEARCH_SCHEMA,
    SOURCE_INGEST_SCHEMA,
    STATUS_SCHEMA,
    TRUST_VERIFY_SCHEMA,
    VERIFY_SCHEMA,
    TotalRecallMemoryProvider,
    register,
)
'''

PLUGIN_README = """# Total Recall Hermes Memory Provider

Total Recall is a local-first continuity authority for Hermes Agent. It keeps
an append-only ledger, signed checkpoints, fail-closed rehydrate, cited recall,
compiled truth, source ingest, freshness checks, temporal graph timelines,
explicit federation, and an evidence-locked Knowledge Engine.

## Tools

The provider exposes Total Recall search/status/checkpoint/verify/trust-verify/learning-review/rehydrate,
working-context source ingest, cited Knowledge Engine query, freshness,
compiled truth, graph inspect/timeline, explicit federation query tools, and
append-only loop inbox/event tools for external workers.
Federation requires explicit authorization and returns workspace-separated
results rather than silently merging another agent's memory. Learning review
returns candidate cards, layer-routing decisions, action boundaries, and a
wake-up diff without mutating the ledger.
Portable-clone restore is deliberately not exposed as a Hermes tool.

## Compaction And Rehydration In Plain English

Use one operator model:

```text
save completed turns -> checkpoint -> verify -> rehydrate cited context
```

Hermes owns when old chat is compacted. Total Recall owns whether memory is safe
to reuse after that context changes. The easiest profile policy is to align the
Hermes compaction threshold and Total Recall auto-rehydrate threshold so both are
one visible **context risk zone**:

```yaml
compression:
  enabled: true
  threshold: 0.55

memory:
  provider: total-recall
  total-recall:
    auto_rehydrate:
      enabled: true
      context_threshold: 0.55
```

If the Total Recall threshold is higher than the Hermes compaction threshold,
treat it as an extra high-context safety net, not a separate memory authority.
The provider still handles Hermes compaction hooks.

What gets saved:

- completed turns through `sync_turn()`
- pre-compaction continuity through `on_pre_compress(messages)`
- session switches/resets/resumes as lifecycle events
- session end plus checkpoint on shutdown
- explicit checkpoint events when Hermes or the user calls the checkpoint tool

Search and rehydrate examples:

```bash
total-recall rehydrate --session-id main --query "active work before compaction"
total-recall search "Total Recall dashboard backup panel"
total-recall knowledge query --query "What was the last verified state before rehydrate?" --format text
total-recall knowledge query --query "What decisions did we make about backup freshness?" --format text
```

## Install

Preferred:

```bash
total-recall hermes install --profile <profile> --activate --format text
total-recall hermes doctor
```

The installer detects Hermes' Python environment, installs or upgrades
`total-recall-core` there when needed, writes the plugin bundle to
`~/.hermes/plugins/memory/total-recall`, also writes the flat
`~/.hermes/plugins/total-recall` compatibility provider path used by Hermes
v0.15.x, selects it as the profile's memory provider, writes aligned Context Risk
Zone defaults (`compression.threshold=0.55`, `auto_rehydrate.enabled=true`,
`auto_rehydrate.context_threshold=0.55`), and verifies Hermes memory status.

Manual fallback:

```bash
mkdir -p ~/.hermes/plugins/memory
cp -R total-recall ~/.hermes/plugins/memory/total-recall
cp -R total-recall ~/.hermes/plugins/total-recall
hermes -p <profile> config set memory.provider total-recall
hermes -p <profile> memory status
```

The Python package `total-recall-core` must be importable in the Python
environment used by Hermes. If auto-detection cannot find that interpreter,
pass `--hermes-python /path/to/hermes/venv/bin/python`.
"""


def plugin_yaml() -> str:
    return (
        f"name: {PLUGIN_NAME}\n"
        f"version: {__version__}\n"
        'entrypoint: "__init__.py"\n'
        'provider_class: "TotalRecallMemoryProvider"\n'
        'requires_python: ">=3.11"\n'
        "python_dependencies:\n"
        f"  - total-recall-core=={__version__}\n"
        'description: "Total Recall - local-first Hermes continuity provider with ledger, signed checkpoints, fail-closed rehydrate, cited Knowledge Engine recall, freshness, temporal graph timeline, source ingest, learning review, compiled truth, and explicit federation."\n'
        "tools:\n"
        "  - total_recall_search\n"
        "  - total_recall_status\n"
        "  - total_recall_checkpoint\n"
        "  - total_recall_verify\n"
        "  - total_recall_trust_verify\n"
        "  - total_recall_learning_review\n"
        "  - total_recall_rehydrate\n"
        "  - total_recall_incidents\n"
        "  - total_recall_source_ingest\n"
        "  - total_recall_knowledge_query\n"
        "  - total_recall_knowledge_freshness\n"
        "  - total_recall_knowledge_status\n"
        "  - total_recall_knowledge_synthesis_status\n"
        "  - total_recall_knowledge_compiled_truth\n"
        "  - total_recall_knowledge_graph_inspect\n"
        "  - total_recall_knowledge_graph_timeline\n"
        "  - total_recall_federation_query\n"
        "  - total_recall_loop_inbox\n"
        "  - total_recall_loop_start\n"
        "  - total_recall_loop_note\n"
        "  - total_recall_loop_verify\n"
        "  - total_recall_loop_complete\n"
        "  - total_recall_handoff_export\n"
    )


def plugin_files() -> Dict[str, str]:
    return {
        "__init__.py": PLUGIN_INIT,
        "plugin.yaml": plugin_yaml(),
        "README.md": PLUGIN_README,
    }


def default_hermes_home() -> Path:
    return Path.home().joinpath(".hermes").expanduser()


def default_plugin_dir(hermes_home: Path | None = None) -> Path:
    if hermes_home is not None:
        return hermes_home / "plugins"
    env_path = os.getenv("HERMES_PLUGIN_DIR")
    if env_path:
        return Path(env_path).expanduser()
    return default_hermes_home() / "plugins"


def _plugin_root(*, hermes_home: str = "", plugin_dir: str = "") -> Path:
    if plugin_dir:
        return Path(plugin_dir).expanduser()
    if hermes_home:
        return default_plugin_dir(Path(hermes_home).expanduser())
    return default_plugin_dir()


def installed_plugin_path(*, hermes_home: str = "", plugin_dir: str = "") -> Path:
    return _plugin_root(hermes_home=hermes_home, plugin_dir=plugin_dir) / "memory" / PLUGIN_NAME


def compatibility_plugin_path(*, hermes_home: str = "", plugin_dir: str = "") -> Path:
    return _plugin_root(hermes_home=hermes_home, plugin_dir=plugin_dir) / PLUGIN_NAME


def standalone_plugin_path(*, hermes_home: str = "", plugin_dir: str = "") -> Path:
    """Backward-compatible name for the flat Hermes v0.15.x provider path."""
    return compatibility_plugin_path(hermes_home=hermes_home, plugin_dir=plugin_dir)


def _looks_like_total_recall_plugin(path: Path) -> bool:
    if path.is_symlink():
        try:
            return _looks_like_total_recall_plugin(path.resolve())
        except OSError:
            return True
    plugin_yaml_path = path / "plugin.yaml"
    init_path = path / "__init__.py"
    try:
        yaml_text = plugin_yaml_path.read_text(encoding="utf-8") if plugin_yaml_path.exists() else ""
        init_text = init_path.read_text(encoding="utf-8") if init_path.exists() else ""
    except OSError:
        return False
    return f"name: {PLUGIN_NAME}" in yaml_text or "TotalRecallMemoryProvider" in init_text


def write_compatibility_plugin(destination: Path, *, mode: str, force: bool = False) -> Dict[str, Any]:
    """Install the flat user-provider path used by Hermes v0.15.x."""
    destination = destination.expanduser()
    if destination.exists() or destination.is_symlink():
        if not _looks_like_total_recall_plugin(destination):
            return {
                "ok": False,
                "status": "foreign_plugin_at_compatibility_path",
                "path": str(destination),
                "message": "A non-Total Recall plugin exists at the flat user provider path.",
            }
        if not force:
            return {
                "ok": True,
                "status": "already_installed",
                "path": str(destination),
                "message": "Flat user-provider compatibility bundle already exists.",
            }
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        else:
            shutil.rmtree(destination)

    if mode == "symlink":
        source = repo_plugin_source()
        if source is None:
            return {"ok": False, "status": "SYMLINK_SOURCE_NOT_FOUND", "path": str(destination)}
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source, target_is_directory=True)
        return {
            "ok": True,
            "status": "installed_symlink",
            "path": str(destination),
            "source": str(source),
            "message": "Installed flat user-provider compatibility symlink for Hermes v0.15.x.",
        }

    result = write_plugin_bundle(destination, force=True)
    result["status"] = "installed_copy" if result.get("ok") else "install_failed"
    result["message"] = "Installed flat user-provider compatibility copy for Hermes v0.15.x."
    return result


def repo_plugin_source() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    candidate = root / "hermes-plugin" / PLUGIN_NAME
    if (candidate / "__init__.py").exists() and (candidate / "plugin.yaml").exists():
        return candidate
    return None


def repo_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").exists() and (root / "src" / "total_recall_core").is_dir():
        return root
    return None


def default_core_install_spec() -> str:
    root = repo_root()
    if root is not None:
        return str(root)
    return f"total-recall-core=={__version__}"


def resolve_hermes_python(*, hermes_bin: str = "hermes", hermes_python: str = "") -> Dict[str, Any]:
    if hermes_python:
        path = Path(hermes_python).expanduser()
        return {
            "ok": path.exists(),
            "status": "PASS" if path.exists() else "HERMES_PYTHON_NOT_FOUND",
            "hermesPython": str(path),
            "source": "explicit",
            "candidates": [str(path)],
        }

    hermes_path_raw = shutil.which(hermes_bin)
    if not hermes_path_raw:
        return {"ok": False, "status": "HERMES_NOT_FOUND", "error": f"{hermes_bin} not found on PATH"}

    hermes_path = Path(hermes_path_raw).expanduser()
    candidates: list[Path] = []
    _append_python_candidate(candidates, hermes_path)
    _append_wrapper_candidates(candidates, hermes_path)

    checked = []
    for candidate in candidates:
        checked.append(str(candidate))
        if candidate.exists() and os.access(candidate, os.X_OK):
            return {
                "ok": True,
                "status": "PASS",
                "hermesPath": str(hermes_path),
                "hermesPython": str(candidate),
                "source": "detected",
                "candidates": checked,
            }
    return {
        "ok": False,
        "status": "HERMES_PYTHON_NOT_FOUND",
        "hermesPath": str(hermes_path),
        "candidates": checked,
        "message": "Could not find the Python executable used by Hermes.",
        "nextSteps": [
            "Pass --hermes-python /path/to/hermes/venv/bin/python.",
            "Run `head -n 5 $(command -v hermes)` to inspect the Hermes wrapper.",
        ],
    }


def _append_python_candidate(candidates: list[Path], executable: Path) -> None:
    parent = executable.resolve().parent
    for name in ("python", "python3"):
        candidate = parent / name
        if candidate not in candidates:
            candidates.append(candidate)


def _append_wrapper_candidates(candidates: list[Path], hermes_path: Path) -> None:
    try:
        text = hermes_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    lines = text.splitlines()
    if lines:
        first = lines[0]
        if first.startswith("#!") and "python" in first and "/env " not in first:
            candidate = Path(first[2:].strip().split()[0]).expanduser()
            if candidate not in candidates:
                candidates.append(candidate)
    for match in re.findall(r"""["']([^"']*/bin/hermes)["']""", text):
        _append_python_candidate(candidates, Path(match).expanduser())
    for match in re.findall(r"""exec\s+([^\s"']*/bin/hermes)""", text):
        _append_python_candidate(candidates, Path(match).expanduser())


def check_core_in_hermes_python(*, hermes_bin: str = "hermes", hermes_python: str = "") -> Dict[str, Any]:
    resolution = resolve_hermes_python(hermes_bin=hermes_bin, hermes_python=hermes_python)
    if not resolution.get("ok"):
        return {
            "ok": False,
            "status": resolution.get("status", "HERMES_PYTHON_NOT_FOUND"),
            "requiredVersion": __version__,
            "resolution": resolution,
            "message": resolution.get("message", "Hermes Python could not be resolved."),
            "nextSteps": resolution.get("nextSteps", []),
        }
    python = str(resolution["hermesPython"])
    code = (
        "import json, sys\n"
        "payload = {'python': sys.executable}\n"
        "try:\n"
        "    import total_recall_core as tr\n"
        "    payload.update({'importable': True, 'version': getattr(tr, '__version__', None)})\n"
        "except Exception as exc:\n"
        "    payload.update({'importable': False, 'error': f'{type(exc).__name__}: {exc}'})\n"
        "print(json.dumps(payload))\n"
    )
    completed = subprocess.run([python, "-c", code], text=True, capture_output=True, check=False)
    payload = _json_from_stdout(completed.stdout)
    importable = bool(payload.get("importable"))
    installed_version = payload.get("version")
    version_ok = installed_version == __version__
    ok = completed.returncode == 0 and importable and version_ok
    if ok:
        status = "AVAILABLE"
        message = f"total-recall-core {installed_version} is importable in Hermes Python."
        next_steps: list[str] = []
    elif importable:
        status = "VERSION_MISMATCH"
        message = f"Hermes Python has total-recall-core {installed_version}; {__version__} is required."
        next_steps = ["Run `total-recall hermes install --core-install always --profile <profile> --activate`."]
    else:
        status = "MISSING"
        message = "Hermes Python cannot import total_recall_core."
        next_steps = ["Run `total-recall hermes install --profile <profile> --activate`."]
    return {
        "ok": ok,
        "status": status,
        "hermesPython": python,
        "requiredVersion": __version__,
        "installedVersion": installed_version,
        "importable": importable,
        "versionOk": version_ok,
        "message": message,
        "nextSteps": next_steps,
        "resolution": resolution,
        "check": {
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        },
    }


def ensure_core_in_hermes_python(
    *,
    hermes_bin: str = "hermes",
    hermes_python: str = "",
    core_source: str = "",
    core_install: str = "auto",
) -> Dict[str, Any]:
    if core_install == "skip":
        return {
            "ok": True,
            "status": "SKIPPED",
            "requiredVersion": __version__,
            "message": "Skipped Hermes Python core check/install.",
        }

    before = check_core_in_hermes_python(hermes_bin=hermes_bin, hermes_python=hermes_python)
    if core_install == "auto" and before.get("ok"):
        return {**before, "install": {"ok": True, "status": "NOT_NEEDED"}}

    if core_install not in {"auto", "always"}:
        return {"ok": False, "status": "INVALID_CORE_INSTALL_MODE", "mode": core_install}

    resolution = before.get("resolution") or resolve_hermes_python(hermes_bin=hermes_bin, hermes_python=hermes_python)
    if not resolution.get("ok"):
        return before

    python = str(resolution["hermesPython"])
    install_spec = core_source or default_core_install_spec()
    pip_ready = _ensure_pip(python)
    if not pip_ready.get("ok"):
        return {
            "ok": False,
            "status": "PIP_UNAVAILABLE",
            "before": before,
            "install": pip_ready,
            "hermesPython": python,
            "installSpec": install_spec,
            "message": "Hermes Python is available, but pip could not be started.",
            "nextSteps": [f"Run `{python} -m ensurepip --upgrade` and retry."],
        }

    command = [python, "-m", "pip", "install", "--upgrade", install_spec]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    after = check_core_in_hermes_python(hermes_bin=hermes_bin, hermes_python=python)
    ok = completed.returncode == 0 and bool(after.get("ok"))
    return {
        "ok": ok,
        "status": "INSTALLED" if ok else "INSTALL_FAILED",
        "requiredVersion": __version__,
        "hermesPython": python,
        "installSpec": install_spec,
        "before": before,
        "after": after,
        "install": {
            "ok": completed.returncode == 0,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        },
        "message": "Installed total-recall-core into Hermes Python." if ok else "Could not install total-recall-core into Hermes Python.",
        "nextSteps": [] if ok else [f"Run `{python} -m pip install --upgrade {install_spec}` and retry."],
    }


def _ensure_pip(python: str) -> Dict[str, Any]:
    probe = subprocess.run([python, "-m", "pip", "--version"], text=True, capture_output=True, check=False)
    if probe.returncode == 0:
        return {"ok": True, "status": "AVAILABLE", "stdout": probe.stdout.strip()}
    bootstrap = subprocess.run([python, "-m", "ensurepip", "--upgrade"], text=True, capture_output=True, check=False)
    if bootstrap.returncode != 0:
        return {
            "ok": False,
            "status": "ENSUREPIP_FAILED",
            "probe": {"returncode": probe.returncode, "stdout": probe.stdout.strip(), "stderr": probe.stderr.strip()},
            "ensurepip": {"returncode": bootstrap.returncode, "stdout": bootstrap.stdout.strip(), "stderr": bootstrap.stderr.strip()},
        }
    verify = subprocess.run([python, "-m", "pip", "--version"], text=True, capture_output=True, check=False)
    return {
        "ok": verify.returncode == 0,
        "status": "AVAILABLE" if verify.returncode == 0 else "PIP_STILL_UNAVAILABLE",
        "stdout": verify.stdout.strip(),
        "stderr": verify.stderr.strip(),
    }


def _json_from_stdout(stdout: str) -> Dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


def write_plugin_bundle(destination: Path, *, force: bool = False) -> Dict[str, Any]:
    destination = destination.expanduser()
    if destination.exists():
        if not force:
            return {"ok": False, "error": "plugin_destination_exists", "path": str(destination)}
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        else:
            shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in plugin_files().items():
        path = destination / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return {"ok": True, "path": str(destination), "files": written}


def install_plugin(
    *,
    hermes_home: str = "",
    plugin_dir: str = "",
    mode: str = "copy",
    force: bool = False,
    dry_run: bool = False,
    profile: str = "",
    activate: bool = False,
    hermes_bin: str = "hermes",
    hermes_python: str = "",
    core_install: str = "auto",
    core_source: str = "",
) -> Dict[str, Any]:
    destination = installed_plugin_path(hermes_home=hermes_home, plugin_dir=plugin_dir)
    compatibility = compatibility_plugin_path(hermes_home=hermes_home, plugin_dir=plugin_dir)
    payload: Dict[str, Any] = {
        "ok": True,
        "plugin": PLUGIN_NAME,
        "version": __version__,
        "kind": "memory-provider",
        "mode": mode,
        "path": str(destination),
        "compatibilityPath": str(compatibility),
        "dryRun": dry_run,
    }
    if dry_run:
        payload["actions"] = [
            "ensure_core_in_hermes_python" if core_install != "skip" else "core_install_skipped",
            "write_docs_memory_provider_bundle",
            "write_flat_user_provider_compatibility_bundle",
            "activate_profile_with_context_risk_defaults" if activate or profile else "activation_skipped",
        ]
        payload["core"] = {
            "mode": core_install,
            "source": core_source or default_core_install_spec(),
            "hermesPython": hermes_python or "<auto-detect>",
            "status": "DRY_RUN",
            "message": "Would check total-recall-core in Hermes Python and install it if needed." if core_install != "skip" else "Would skip Hermes Python core install.",
        }
        return payload

    payload["core"] = ensure_core_in_hermes_python(
        hermes_bin=hermes_bin,
        hermes_python=hermes_python,
        core_source=core_source,
        core_install=core_install,
    )
    payload["ok"] = payload["ok"] and bool(payload["core"].get("ok"))
    if not payload["ok"]:
        return payload

    if mode == "symlink":
        source = repo_plugin_source()
        if source is None:
            return {"ok": False, "error": "symlink_mode_requires_repo_checkout"}
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            if not force:
                return {"ok": False, "error": "plugin_destination_exists", "path": str(destination)}
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            else:
                shutil.rmtree(destination)
        destination.symlink_to(source, target_is_directory=True)
        payload["install"] = {"ok": True, "path": str(destination), "source": str(source)}
    else:
        payload["install"] = write_plugin_bundle(destination, force=force)
        payload["ok"] = bool(payload["install"].get("ok"))
        if not payload["ok"]:
            return payload

    payload["validation"] = validate_plugin_bundle(destination, strict_clean=(mode != "symlink"))
    payload["ok"] = payload["ok"] and bool(payload["validation"].get("ok"))
    payload["compatibilityInstall"] = write_compatibility_plugin(compatibility, mode=mode, force=force)
    payload["ok"] = payload["ok"] and bool(payload["compatibilityInstall"].get("ok"))
    payload["compatibilityValidation"] = validate_plugin_bundle(compatibility, strict_clean=(mode != "symlink"))
    payload["ok"] = payload["ok"] and bool(payload["compatibilityValidation"].get("ok"))

    should_activate = bool(activate or profile)
    if should_activate:
        payload["activation"] = activate_profile(profile=profile or "default", hermes_bin=hermes_bin)
        payload["ok"] = payload["ok"] and bool(payload["activation"].get("ok"))
    else:
        payload["activation"] = {
            "ok": True,
            "status": "SKIPPED",
            "commands": [
                f"hermes -p <profile> config set memory.provider {PLUGIN_NAME}",
                f"hermes -p <profile> config set compression.threshold {DEFAULT_CONTEXT_RISK_THRESHOLD}",
                "hermes -p <profile> config set memory.total-recall.auto_rehydrate.enabled true",
                f"hermes -p <profile> config set memory.total-recall.auto_rehydrate.context_threshold {DEFAULT_CONTEXT_RISK_THRESHOLD}",
                "hermes -p <profile> memory status",
            ],
        }
    return payload


def validate_plugin_bundle(path: Path, *, strict_clean: bool = True) -> Dict[str, Any]:
    path = path.expanduser()
    required = ["__init__.py", "plugin.yaml", "README.md"]
    missing = [name for name in required if not (path / name).is_file()]
    cache_artifacts = []
    forbidden = []
    for item in path.rglob("*"):
        if item.name == ".DS_Store" or "__pycache__" in item.parts or item.suffix == ".pyc":
            cache_artifacts.append(str(item))
            if strict_clean:
                forbidden.append(str(item))
    init_text = (path / "__init__.py").read_text(encoding="utf-8") if (path / "__init__.py").exists() else ""
    yaml_text = (path / "plugin.yaml").read_text(encoding="utf-8") if (path / "plugin.yaml").exists() else ""
    ok = not missing and not forbidden and "register" in init_text and f"name: {PLUGIN_NAME}" in yaml_text
    return {
        "ok": ok,
        "path": str(path),
        "strictClean": strict_clean,
        "missing": missing,
        "forbidden": forbidden,
        "cacheArtifacts": cache_artifacts,
        "hasRegister": "register" in init_text,
        "hasName": f"name: {PLUGIN_NAME}" in yaml_text,
    }


def bundle_plugin(*, out: str, force: bool = False) -> Dict[str, Any]:
    out_path = Path(out).expanduser()
    if out_path.exists() and not force:
        return {"ok": False, "error": "bundle_exists", "path": str(out_path)}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="total-recall-hermes-plugin-") as tmp:
        memory_root = Path(tmp) / "memory" / PLUGIN_NAME
        compat_root = Path(tmp) / PLUGIN_NAME
        written = write_plugin_bundle(memory_root, force=True)
        if not written.get("ok"):
            return written
        compat_written = write_plugin_bundle(compat_root, force=True)
        if not compat_written.get("ok"):
            return compat_written
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(memory_root, arcname=f"memory/{PLUGIN_NAME}")
            tar.add(compat_root, arcname=PLUGIN_NAME)
    return {"ok": True, "bundle": str(out_path), "plugin": PLUGIN_NAME, "kind": "memory-provider", "version": __version__}


def activate_profile(*, profile: str, hermes_bin: str = "hermes") -> Dict[str, Any]:
    hermes_path = shutil.which(hermes_bin)
    if not hermes_path:
        return {"ok": False, "status": "HERMES_NOT_FOUND", "error": f"{hermes_bin} not found on PATH"}
    commands = [
        {
            "command": [hermes_path, "-p", profile, "config", "set", "memory.provider", PLUGIN_NAME],
            "optional": False,
            "status": "CONFIG_SET_FAILED",
        },
        {
            "command": [hermes_path, "-p", profile, "config", "set", "compression.threshold", DEFAULT_CONTEXT_RISK_THRESHOLD],
            "optional": False,
            "status": "COMPRESSION_THRESHOLD_SET_FAILED",
        },
        {
            "command": [hermes_path, "-p", profile, "config", "set", "memory.total-recall.auto_rehydrate.enabled", "true"],
            "optional": False,
            "status": "AUTO_REHYDRATE_ENABLE_FAILED",
        },
        {
            "command": [hermes_path, "-p", profile, "config", "set", "memory.total-recall.auto_rehydrate.context_threshold", DEFAULT_CONTEXT_RISK_THRESHOLD],
            "optional": False,
            "status": "AUTO_REHYDRATE_THRESHOLD_SET_FAILED",
        },
        {
            "command": [hermes_path, "-p", profile, "memory", "status"],
            "optional": False,
            "status": "MEMORY_STATUS_FAILED",
        },
    ]
    results = []
    for item in commands:
        command = item["command"]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        results.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
            }
        )
        if completed.returncode != 0:
            if item["optional"] and _is_missing_plugins_command(stdout=stdout, stderr=stderr):
                continue
            return {"ok": False, "status": item["status"], "profile": profile, "results": results}
        if "NOT installed" in stdout:
            return {"ok": False, "status": "PLUGIN_NOT_INSTALLED", "profile": profile, "results": results}
    return {
        "ok": True,
        "status": "PASS",
        "profile": profile,
        "contextRiskPolicy": {
            "compression.threshold": DEFAULT_CONTEXT_RISK_THRESHOLD,
            "memory.total-recall.auto_rehydrate.enabled": True,
            "memory.total-recall.auto_rehydrate.context_threshold": DEFAULT_CONTEXT_RISK_THRESHOLD,
        },
        "results": results,
    }


def _is_missing_plugins_command(*, stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return "invalid choice" in text or "no such command" in text or "unknown command" in text


def status(
    *,
    hermes_home: str = "",
    plugin_dir: str = "",
    hermes_bin: str = "hermes",
    hermes_python: str = "",
    check_core: bool = True,
) -> Dict[str, Any]:
    path = installed_plugin_path(hermes_home=hermes_home, plugin_dir=plugin_dir)
    compatibility = compatibility_plugin_path(hermes_home=hermes_home, plugin_dir=plugin_dir)
    validation = validate_plugin_bundle(path, strict_clean=False) if path.exists() else {"ok": False, "missing": ["plugin_directory"], "path": str(path)}
    compatibility_validation = validate_plugin_bundle(compatibility, strict_clean=False) if compatibility.exists() or compatibility.is_symlink() else {"ok": False, "missing": ["compatibility_plugin_directory"], "path": str(compatibility)}
    core = check_core_in_hermes_python(hermes_bin=hermes_bin, hermes_python=hermes_python) if check_core else {
        "ok": True,
        "status": "SKIPPED",
        "message": "Core runtime check skipped.",
    }
    compatibility_installed = compatibility.exists() or compatibility.is_symlink()
    compatibility_ours = compatibility_installed and _looks_like_total_recall_plugin(compatibility)
    ready = bool(validation.get("ok")) and bool(compatibility_validation.get("ok")) and bool(core.get("ok")) and bool(compatibility_ours)
    return {
        "ok": ready,
        "ready": ready,
        "plugin": PLUGIN_NAME,
        "kind": "memory-provider",
        "version": __version__,
        "path": str(path),
        "installed": path.exists(),
        "compatibilityPath": str(compatibility),
        "compatibilityInstalled": compatibility_installed,
        "compatibilityLooksLikeTotalRecall": bool(compatibility_ours),
        "validation": validation,
        "compatibilityValidation": compatibility_validation,
        "core": core,
        "nextSteps": _status_next_steps(validation=validation, compatibility_validation=compatibility_validation, core=core, compatibility_ours=bool(compatibility_ours)),
    }


def _status_next_steps(*, validation: Dict[str, Any], compatibility_validation: Dict[str, Any], core: Dict[str, Any], compatibility_ours: bool = False) -> list[str]:
    steps: list[str] = []
    if not validation.get("ok"):
        steps.append("Run `total-recall hermes install --force` to write a clean Hermes plugin bundle.")
    if not compatibility_validation.get("ok") or not compatibility_ours:
        steps.append("Run `total-recall hermes install --force` to write the flat user-provider compatibility bundle used by Hermes v0.15.x.")
    if not core.get("ok"):
        steps.extend(core.get("nextSteps") or ["Run `total-recall hermes install --core-install always`."])
    return steps


def pretty_install_command(profile: str = "<profile>") -> str:
    return f"total-recall hermes install --profile {profile} --activate"


def dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
