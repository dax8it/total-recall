from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from total_recall_core import TotalRecallConfig, TotalRecallCore


ROOT = Path(__file__).resolve().parents[1]


def test_catalog_manifests_do_not_drift():
    # Layout comments may differ; every declaration must otherwise match.
    def declarations(path):
        return "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )

    assert declarations(ROOT / "plugin.yaml") == declarations(ROOT / "src/plugin.yaml")


def test_catalog_entrypoints_do_not_drift():
    root = (ROOT / "__init__.py").read_text(encoding="utf-8")
    source = (ROOT / "src/__init__.py").read_text(encoding="utf-8")
    # Only comments/formatting and this exact layout-dependent path may differ.
    root_path = '_source = Path(__file__).resolve().parent / "src"'
    source_path = '_source = Path(__file__).resolve().parent'
    assert root.count(root_path) == 1
    assert source.count(source_path) == 1
    normalized = root.replace(root_path, source_path, 1)
    assert ast.dump(ast.parse(normalized)) == ast.dump(ast.parse(source))


@pytest.mark.parametrize("with_host", [False, True])
@pytest.mark.parametrize("layout", ["root", "src"])
def test_catalog_entrypoint_loads_bundled_core_without_installed_package(tmp_path, with_host, layout):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    source = ROOT if layout == "root" else ROOT / "src"
    core_parent = plugin / "src" if layout == "root" else plugin
    shutil.copy2(source / "__init__.py", plugin / "__init__.py")
    shutil.copytree(ROOT / "src" / "total_recall_core", core_parent / "total_recall_core", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for outside in (tmp_path / "src", tmp_path / "override"):
        (outside / "total_recall_core").mkdir(parents=True)
        (outside / "total_recall_core" / "__init__.py").write_text("raise AssertionError('outside core loaded')\n")
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
    assert pathlib.Path(implementation.__file__).is_relative_to(pathlib.Path(sys.argv[3]))
    assert len(provider.get_tool_schemas()) > 0
    assert str(root.parent / "src") not in sys.path
    assert str(root.parent / "override") not in sys.path
print(json.dumps({"ok": True}))
'''
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["TOTAL_RECALL_CORE_SRC"] = str(tmp_path / "override")
    result = subprocess.run(
        [sys.executable, "-P", "-B", "-c", code, str(plugin), str(with_host), str(core_parent)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"ok": True}


@pytest.mark.parametrize("directory", [ROOT, ROOT / "src"])
def test_catalog_manifest_declares_provider_tools_and_optional_secrets(monkeypatch, directory):
    from test_hermes_plugin import _load_plugin

    module = _load_plugin(monkeypatch)
    manifest = (directory / "plugin.yaml").read_text(encoding="utf-8")
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "license: MIT\n" in manifest
    assert 'requires_hermes: ">=0.23.1"\n' in manifest
    dependency = "cryptography>=42,<51"
    assert f'  - "{dependency}"' in manifest
    assert dependency in package["project"]["dependencies"]
    assert 'entrypoint: "__init__.py"' in manifest
    assert not any("total-recall-core" in line for line in manifest.splitlines() if line.startswith("  - "))
    assert "provides_tools: []\n" in manifest
    assert "\ntools:\n" not in manifest
    for name in ("BACKUP", "PORTABLE_CLONE"):
        assert f"type: secret\n    env: TOTAL_RECALL_{name}_PASSPHRASE\n    required: false" in manifest
    # A context exposing only memory registration proves register() does not
    # claim direct tool registrations. The provider's schemas are documented.
    import types
    providers = []
    module.register(types.SimpleNamespace(register_memory_provider=providers.append))
    assert len(providers) == 1
    tools = {schema["name"] for schema in providers[0].get_tool_schemas()}
    documented = set(re.findall(r"^- `(total_recall_\w+)`$", (ROOT / "src" / "README.md").read_text(), re.MULTILINE))
    assert tools == documented
    assert len(tools) == 23


def test_catalog_subdirectory_excludes_development_tree():
    source = ROOT / "src"
    for excluded in ("tests", "docs", "scripts", "uv.lock", "hermes-plugin"):
        assert (ROOT / excluded).exists(), f"development checkout lost {excluded}"
        assert not (source / excluded).exists()
    for required in ("plugin.yaml", "__init__.py", "README.md", "LICENSE", "total_recall_core/__init__.py"):
        assert (source / required).is_file()
        assert not (source / required).is_symlink()
    assert (source / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()


@pytest.mark.parametrize("layout", ["root", "src"])
def test_catalog_missing_bundle_does_not_load_a_site_package(tmp_path, layout):
    source = ROOT if layout == "root" else ROOT / "src"
    shutil.copy2(source / "__init__.py", tmp_path / "__init__.py")
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import runpy, sys; runpy.run_path(sys.argv[1])", str(tmp_path / "__init__.py")],
        cwd=tmp_path, text=True, capture_output=True, check=False,
    )
    assert result.returncode != 0
    assert "missing its bundled core" in result.stderr


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
