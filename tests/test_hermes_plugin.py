from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


class _FakeMemoryProvider:
    @property
    def name(self):
        return "fake"

    def is_available(self):
        return True

    def initialize(self, session_id: str, **kwargs):
        return None

    def get_tool_schemas(self):
        return []


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


def _load_plugin(monkeypatch):
    _install_fake_hermes_modules(monkeypatch)
    plugin_path = Path(__file__).resolve().parents[1] / "hermes-plugin" / "total-recall" / "__init__.py"
    spec = importlib.util.spec_from_file_location("total_recall_hermes_plugin_test", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_plugin_lifecycle_sync_prefetch_and_tools(tmp_path, monkeypatch):
    module = _load_plugin(monkeypatch)
    provider = module.TotalRecallMemoryProvider()
    provider.initialize("s1", hermes_home=str(tmp_path))

    assert provider.name == "total-recall"
    assert provider.is_available() is True
    assert "Total Recall is active" in provider.system_prompt_block()
    assert {schema["name"] for schema in provider.get_tool_schemas()} >= {
        "total_recall_search",
        "total_recall_status",
        "total_recall_checkpoint",
        "total_recall_verify",
        "total_recall_rehydrate",
        "total_recall_incidents",
    }

    provider.sync_turn("remember plugin lifecycle", "stored", session_id="s1")
    result = json.loads(provider.handle_tool_call("total_recall_search", {"query": "plugin lifecycle"}))
    assert result["ok"] is True
    assert result["results"]

    checkpoint = json.loads(provider.handle_tool_call("total_recall_checkpoint", {"session_id": "s1"}))
    assert checkpoint["ok"] is True
    verified = json.loads(provider.handle_tool_call("total_recall_verify", {"session_id": "s1"}))
    assert verified["status"] == "PASS"
    rehydrated = json.loads(provider.handle_tool_call("total_recall_rehydrate", {"session_id": "s1", "query": "plugin lifecycle"}))
    assert rehydrated["ok"] is True
    assert "Total Recall Rehydrate Authority" in rehydrated["context_block"]


def test_plugin_pre_compress_and_session_switch_schedule_auto_rehydrate(tmp_path, monkeypatch):
    module = _load_plugin(monkeypatch)
    provider = module.TotalRecallMemoryProvider()
    provider.initialize("s1", hermes_home=str(tmp_path))
    provider.sync_turn("critical file path src/app.py", "noted", session_id="s1")
    provider.handle_tool_call("total_recall_checkpoint", {"session_id": "s1"})

    block = provider.on_pre_compress([
        {"role": "user", "content": "critical file path src/app.py"},
        {"role": "assistant", "content": "noted"},
    ])
    assert "Total Recall" in block
    assert "src/app.py" in block

    provider.on_session_switch("s2", parent_session_id="s1", reset=True, reason="new_session")
    auto = provider.prefetch("continue from prior work", session_id="s2")
    assert "Total Recall Auto Rehydrate" in auto
    assert "reason: after_new_session" in auto


def test_plugin_context_threshold_auto_rehydrate(tmp_path, monkeypatch):
    module = _load_plugin(monkeypatch)
    provider = module.TotalRecallMemoryProvider()
    provider.initialize("s1", hermes_home=str(tmp_path))
    provider.sync_turn("threshold continuity marker", "stored", session_id="s1")
    provider.handle_tool_call("total_recall_checkpoint", {"session_id": "s1"})

    provider.on_turn_start(1, "continue", prompt_tokens=80, context_length=100, context_usage_ratio=0.8)
    auto = provider.prefetch("threshold continuity", session_id="s1")
    assert "Total Recall Auto Rehydrate" in auto
    assert "reason: context_usage_threshold" in auto


def test_plugin_auto_rehydrate_fails_closed_on_tampered_anchor(tmp_path, monkeypatch):
    module = _load_plugin(monkeypatch)
    provider = module.TotalRecallMemoryProvider()
    provider.initialize("s1", hermes_home=str(tmp_path))
    provider.sync_turn("tamper protected memory", "stored", session_id="s1")
    checkpoint = json.loads(provider.handle_tool_call("total_recall_checkpoint", {"session_id": "s1"}))

    anchor_path = Path(tmp_path) / "total-recall" / "anchors" / f"{checkpoint['checkpoint']['checkpoint_id']}.json"
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchor["signature"] = "bad-signature"
    anchor_path.write_text(json.dumps(anchor), encoding="utf-8")

    provider.on_session_switch("s2", parent_session_id="s1", reset=False, reason="resume")
    auto = provider.prefetch("tamper protected memory", session_id="s2")
    assert "status: FAIL_CLOSED" in auto
    assert "anchor_signature_mismatch" in auto
