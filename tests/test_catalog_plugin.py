from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from total_recall_core import TotalRecallConfig, TotalRecallCore


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("with_host", [False, True])
def test_catalog_entrypoint_loads_bundled_core_without_installed_package(tmp_path, with_host):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    shutil.copy2(ROOT / "__init__.py", plugin / "__init__.py")
    shutil.copytree(ROOT / "src" / "total_recall_core", plugin / "src" / "total_recall_core", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    code = '''
import importlib.util, json, pathlib, sys, types
root = pathlib.Path(sys.argv[1])
assert "total_recall_core" not in sys.modules
spec = importlib.util.spec_from_file_location("catalog_plugin", root / "__init__.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert callable(module.register)
assert "total_recall_core.hermes_provider" not in sys.modules
assert "agent" not in sys.modules
if sys.argv[2] == "True":
    agent = types.ModuleType("agent")
    memory = types.ModuleType("agent.memory_provider")
    memory.MemoryProvider = type("MemoryProvider", (), {})
    tools = types.ModuleType("tools")
    registry = types.ModuleType("tools.registry")
    registry.tool_error = lambda message: json.dumps({"error": message})
    sys.modules.update({"agent": agent, "agent.memory_provider": memory, "tools": tools, "tools.registry": registry})
    provider = module.TotalRecallMemoryProvider()
    registered = []
    module.register(types.SimpleNamespace(register_memory_provider=registered.append))
    assert provider.name == "total-recall"
    assert len(registered) == 1 and registered[0].name == "total-recall"
    import total_recall_core.hermes_provider as implementation
    assert pathlib.Path(implementation.__file__).is_relative_to(root / "src")
    assert len(provider.get_tool_schemas()) > 0
print(json.dumps({"ok": True}))
'''
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("TOTAL_RECALL_CORE_SRC", None)
    result = subprocess.run(
        [sys.executable, "-P", "-B", "-c", code, str(plugin), str(with_host)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"ok": True}


def test_catalog_manifest_declares_license_bounded_dependency_and_all_tools(monkeypatch):
    # The manifest is deliberately a root plugin: a subdirectory-only install
    # would omit the bundled source. Full Hermes validation belongs to its host.
    from test_hermes_plugin import _load_plugin

    module = _load_plugin(monkeypatch)
    manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "license: MIT\n" in manifest
    dependency = "cryptography>=42,<51"
    assert f'  - "{dependency}"' in manifest
    assert dependency in package["project"]["dependencies"]
    assert 'entrypoint: "__init__.py"' in manifest
    assert not any("total-recall-core" in line for line in manifest.splitlines() if line.startswith("  - "))
    tools = {line.removeprefix("  - ") for line in manifest.splitlines() if line.startswith("  - total_recall_")}
    assert tools == {schema["name"] for schema in module.ALL_SCHEMAS}


@pytest.mark.parametrize("middle", ["previous", "all previous", None])
def test_injection_signatures_still_flag_sources_after_literal_split(tmp_path, middle):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_qmd=False, enable_lancedb=False))
    text = "Ordinary project decision." if middle is None else " ".join(["ignore", middle, "instructions"])
    event = core.ingest(kind="note", text=text, session_id="signature")
    rebuilt = core.knowledge_index_rebuild()
    assert rebuilt["ok"] is True
    with sqlite3.connect(tmp_path / "knowledge" / "index" / "knowledge.sqlite") as connection:
        row = connection.execute("SELECT injection_flags_json FROM sources WHERE event_id = ?", (event["event"]["event_id"],)).fetchone()
    assert row is not None
    assert json.loads(row[0]) == ([] if middle is None else [text])
    if middle is not None:
        assert text not in (ROOT / "src" / "total_recall_core" / "knowledge.py").read_text(encoding="utf-8")
