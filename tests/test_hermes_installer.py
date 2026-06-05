from __future__ import annotations

import importlib.util
import json
import os
import sys
import tarfile
import types
from pathlib import Path

from total_recall_core import hermes_installer


class _FakeMemoryProvider:
    @property
    def name(self):
        return "fake"


def _install_fake_hermes_modules(monkeypatch):
    agent_mod = types.ModuleType("agent")
    memory_provider_mod = types.ModuleType("agent.memory_provider")
    memory_provider_mod.MemoryProvider = _FakeMemoryProvider
    tools_mod = types.ModuleType("tools")
    registry_mod = types.ModuleType("tools.registry")
    registry_mod.tool_error = lambda msg: json.dumps({"ok": False, "error": msg})
    monkeypatch.setitem(sys.modules, "agent", agent_mod)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", memory_provider_mod)
    monkeypatch.setitem(sys.modules, "tools", tools_mod)
    monkeypatch.setitem(sys.modules, "tools.registry", registry_mod)


def _load_plugin(path: Path):
    spec = importlib.util.spec_from_file_location("total_recall_installed_plugin_test", path / "__init__.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_hermes_installer_writes_clean_importable_bundle(tmp_path, monkeypatch):
    _install_fake_hermes_modules(monkeypatch)
    hermes_home = tmp_path / "hermes"
    compatibility_path = hermes_home / "plugins" / "total-recall"
    compatibility_path.mkdir(parents=True)
    (compatibility_path / "plugin.yaml").write_text("name: total-recall\n", encoding="utf-8")
    (compatibility_path / "__init__.py").write_text("TotalRecallMemoryProvider = object\n", encoding="utf-8")

    installed = hermes_installer.install_plugin(hermes_home=str(hermes_home), force=True, core_install="skip")

    assert installed["ok"] is True
    assert installed["kind"] == "memory-provider"
    assert installed["compatibilityInstall"]["status"] == "installed_copy"
    assert compatibility_path.exists()
    assert (compatibility_path / "plugin.yaml").is_file()
    plugin_path = Path(installed["path"])
    assert (plugin_path / "__init__.py").is_file()
    assert (plugin_path / "plugin.yaml").is_file()
    assert (plugin_path / "README.md").is_file()
    assert not list(plugin_path.rglob(".DS_Store"))
    assert not list(plugin_path.rglob("*.pyc"))
    assert not list(plugin_path.rglob("__pycache__"))

    module = _load_plugin(plugin_path)
    provider = module.TotalRecallMemoryProvider()
    assert provider.name == "total-recall"
    assert "total_recall_knowledge_query" in {schema["name"] for schema in provider.get_tool_schemas()}


def test_hermes_installer_default_path_matches_global_hermes_plugins(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("HERMES_PLUGIN_DIR", raising=False)

    path = hermes_installer.installed_plugin_path()

    assert path == tmp_path / ".hermes" / "plugins" / "memory" / "total-recall"


def test_hermes_installer_honors_explicit_hermes_home(tmp_path):
    path = hermes_installer.installed_plugin_path(hermes_home=str(tmp_path / "custom-hermes"))

    assert path == tmp_path / "custom-hermes" / "plugins" / "memory" / "total-recall"


def test_hermes_status_tolerates_runtime_python_cache(tmp_path):
    hermes_home = tmp_path / "hermes"
    installed = hermes_installer.install_plugin(hermes_home=str(hermes_home), force=True, core_install="skip")
    plugin_path = Path(installed["path"])
    cache = plugin_path / "__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-311.pyc").write_bytes(b"cache")

    strict = hermes_installer.validate_plugin_bundle(plugin_path)
    status = hermes_installer.status(hermes_home=str(hermes_home), check_core=False)

    assert strict["ok"] is False
    assert status["ok"] is True
    assert status["validation"]["cacheArtifacts"]


def test_hermes_python_detection_reads_wrapper_exec(tmp_path):
    venv = tmp_path / "hermes-agent" / "venv" / "bin"
    venv.mkdir(parents=True)
    hermes_target = venv / "hermes"
    hermes_python = venv / "python"
    hermes_target.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    hermes_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    hermes_target.chmod(0o755)
    hermes_python.chmod(0o755)
    wrapper = tmp_path / "hermes"
    wrapper.write_text(f'#!/usr/bin/env bash\nexec "{hermes_target}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)

    result = hermes_installer.resolve_hermes_python(hermes_bin=str(wrapper))

    assert result["ok"] is True
    assert result["hermesPython"] == str(hermes_python)

    wrapper.write_text(f"#!/usr/bin/env bash\nexec {hermes_target} \"$@\"\n", encoding="utf-8")
    result = hermes_installer.resolve_hermes_python(hermes_bin=str(wrapper))

    assert result["ok"] is True
    assert result["hermesPython"] == str(hermes_python)


def test_hermes_core_auto_installs_when_missing(tmp_path):
    state = tmp_path / "installed.json"
    calls = tmp_path / "calls.jsonl"
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    hermes_target = venv / "hermes"
    hermes_python = venv / "python"
    hermes_target.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    hermes_python.write_text(
        f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

state = Path({str(state)!r})
calls = Path({str(calls)!r})
calls.open("a", encoding="utf-8").write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:2] == ["-c"]:
    if state.exists():
        print(json.dumps({{"python": sys.executable, "importable": True, "version": "1.3.0"}}))
    else:
        print(json.dumps({{"python": sys.executable, "importable": False, "error": "ModuleNotFoundError: No module named total_recall_core"}}))
elif sys.argv[1:4] == ["-m", "pip", "--version"]:
    print("pip 99")
elif sys.argv[1:4] == ["-m", "pip", "install"]:
    state.write_text("installed", encoding="utf-8")
    print("installed")
else:
    print("unexpected", sys.argv[1:])
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    hermes_target.chmod(0o755)
    hermes_python.chmod(0o755)
    wrapper = tmp_path / "hermes"
    wrapper.write_text(f'#!/usr/bin/env bash\nexec "{hermes_target}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)

    result = hermes_installer.ensure_core_in_hermes_python(
        hermes_bin=str(wrapper),
        core_install="auto",
        core_source="/tmp/total-recall-core-test-source",
    )

    assert result["ok"] is True
    assert result["status"] == "INSTALLED"
    recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert ["-m", "pip", "install", "--upgrade", "/tmp/total-recall-core-test-source"] in recorded


def test_hermes_activation_selects_memory_provider(tmp_path, monkeypatch):
    calls = tmp_path / "calls.jsonl"
    hermes = tmp_path / "hermes"
    hermes.write_text(
        f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

Path({str(calls)!r}).open("a", encoding="utf-8").write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:] == ["-p", "smoke", "config", "set", "memory.provider", "total-recall"]:
    print("configured")
elif sys.argv[1:] == ["-p", "smoke", "memory", "status"]:
    print("Plugin: installed OK")
    print("Status: available OK")
else:
    print("unexpected", sys.argv[1:])
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    hermes.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path), os.environ.get("PATH", "")]))

    result = hermes_installer.activate_profile(profile="smoke", hermes_bin="hermes")

    assert result["ok"] is True
    recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert recorded == [
        ["-p", "smoke", "config", "set", "memory.provider", "total-recall"],
        ["-p", "smoke", "memory", "status"],
    ]


def test_hermes_bundle_tarball_contains_complete_plugin(tmp_path):
    bundle = tmp_path / "total-recall-hermes-plugin.tar.gz"
    result = hermes_installer.bundle_plugin(out=str(bundle))

    assert result["ok"] is True
    with tarfile.open(bundle, "r:gz") as tar:
        names = set(tar.getnames())
    assert "memory/total-recall/__init__.py" in names
    assert "memory/total-recall/plugin.yaml" in names
    assert "memory/total-recall/README.md" in names
    assert "total-recall/__init__.py" in names
    assert "total-recall/plugin.yaml" in names
    assert "total-recall/README.md" in names
    assert not any(".DS_Store" in name or "__pycache__" in name or name.endswith(".pyc") for name in names)


def test_repo_plugin_wrapper_matches_embedded_installer_bundle():
    repo_plugin = Path(__file__).resolve().parents[1] / "hermes-plugin" / "total-recall"
    assert (repo_plugin / "__init__.py").read_text(encoding="utf-8") == hermes_installer.plugin_files()["__init__.py"]
    assert (repo_plugin / "plugin.yaml").read_text(encoding="utf-8") == hermes_installer.plugin_files()["plugin.yaml"]
    assert (repo_plugin / "README.md").read_text(encoding="utf-8") == hermes_installer.plugin_files()["README.md"]
