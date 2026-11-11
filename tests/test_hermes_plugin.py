from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest


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
    secret_scope_mod = types.ModuleType("agent.secret_scope")
    setattr(secret_scope_mod, "get_secret", os.getenv)
    tools_mod = types.ModuleType("tools")
    registry_mod = types.ModuleType("tools.registry")
    registry_mod.tool_error = lambda msg: json.dumps({"ok": False, "error": msg})
    monkeypatch.setitem(sys.modules, "agent", agent_mod)
    monkeypatch.setitem(sys.modules, "agent.memory_provider", memory_provider_mod)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", secret_scope_mod)
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
        "total_recall_trust_verify",
        "total_recall_learning_review",
        "total_recall_rehydrate",
        "total_recall_incidents",
        "total_recall_source_ingest",
        "total_recall_knowledge_query",
        "total_recall_knowledge_freshness",
        "total_recall_knowledge_status",
        "total_recall_knowledge_synthesis_status",
        "total_recall_knowledge_compiled_truth",
        "total_recall_knowledge_graph_inspect",
        "total_recall_knowledge_graph_timeline",
        "total_recall_federation_query",
        "total_recall_loop_inbox",
        "total_recall_loop_start",
        "total_recall_loop_note",
        "total_recall_loop_verify",
        "total_recall_loop_complete",
        "total_recall_handoff_export",
    }

    provider.sync_turn("remember plugin lifecycle", "stored", session_id="s1")
    multiline_source_text = (
        "Decision: Plugin promise is day-one install.\n"
        "Quote: \"day-one / install\" should survive JSON transport.\n"
        "Markdown-ish: [launch](https://example.test/path?q=1) and *bold* text.\n"
        "Budget: USD 2,560.20."
    )
    source = json.loads(
        provider.handle_tool_call(
            "total_recall_source_ingest",
            {
                "source_type": "meeting",
                "text": multiline_source_text,
                "title": "Plugin Launch Review",
                "occurred_at": "2026-01-01T00:00:00Z",
                "scope": "public",
            },
        )
    )
    assert source["ok"] is True
    assert source["event"]["kind"] == "source_meeting"
    assert multiline_source_text in source["event"]["text"]
    ledger_lines = (tmp_path / "total-recall" / "ledger" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 2
    assert [json.loads(line)["kind"] for line in ledger_lines] == ["turn", "source_meeting"]

    result = json.loads(provider.handle_tool_call("total_recall_search", {"query": "plugin lifecycle"}))
    assert result["ok"] is True
    assert result["results"]

    checkpoint = json.loads(provider.handle_tool_call("total_recall_checkpoint", {"session_id": "s1"}))
    assert checkpoint["ok"] is True
    learning = json.loads(provider.handle_tool_call("total_recall_learning_review", {"session_id": "s1", "persist": False}))
    assert learning["ok"] is True
    assert learning["schema"] == "total-recall-learning-review-v1"
    assert learning["reviewFile"] is None
    verified = json.loads(provider.handle_tool_call("total_recall_verify", {"session_id": "s1"}))
    assert verified["status"] == "PASS"
    rehydrated = json.loads(provider.handle_tool_call("total_recall_rehydrate", {"session_id": "s1", "query": "plugin lifecycle"}))
    assert rehydrated["ok"] is True
    assert "Total Recall Rehydrate Authority" in rehydrated["context_block"]
    handoff = json.loads(provider.handle_tool_call("total_recall_handoff_export", {"session_id": "s1", "turns": 5}))
    assert handoff["ok"] is True
    assert handoff["packetFile"]

    knowledge = json.loads(
        provider.handle_tool_call(
            "total_recall_knowledge_query",
            {"query": "plugin lifecycle", "session_id": "s1", "mode": "explore"},
        )
    )
    assert knowledge["ok"] is True
    assert knowledge["citations"]
    assert knowledge["providerCalls"][0]["provider"] == "local-hash-rerank"

    freshness = json.loads(provider.handle_tool_call("total_recall_knowledge_freshness", {"entity": "plugin promise", "category": "promise"}))
    assert freshness["ok"] is True
    assert freshness["items"]
    assert freshness["items"][0]["subject"] == "Plugin promise"

    truth = json.loads(provider.handle_tool_call("total_recall_knowledge_compiled_truth", {"action": "show", "format": "md"}))
    assert truth["ok"] is True
    assert "plugin lifecycle" in truth["text"]

    graph = json.loads(provider.handle_tool_call("total_recall_knowledge_graph_inspect", {"entity": "lifecycle"}))
    assert graph["ok"] is True
    assert graph["entities"]
    assert graph["citations"]

    timeline = json.loads(provider.handle_tool_call("total_recall_knowledge_graph_timeline", {"entity": "plugin promise"}))
    assert timeline["ok"] is True
    assert timeline["timeline"]

    fed = json.loads(provider.handle_tool_call("total_recall_federation_query", {"query": "plugin promise"}))
    assert fed["ok"] is True
    assert fed["federation"]["status"] == "NOT_REQUESTED"

    loop_start = json.loads(
        provider.handle_tool_call(
            "total_recall_loop_start",
            {"goal": "Plugin loop evidence", "project": "total-recall", "agent": "sparky", "evidence": ["pytest tests/test_hermes_plugin.py"]},
        )
    )
    assert loop_start["ok"] is True
    loop_id = loop_start["loop"]["loop_id"]
    loop_note = json.loads(provider.handle_tool_call("total_recall_loop_note", {"loop_id": loop_id, "text": "Plugin tool recorded progress."}))
    assert loop_note["ok"] is True
    loop_verify = json.loads(provider.handle_tool_call("total_recall_loop_verify", {"loop_id": loop_id, "status": "PASS", "summary": "Verified via plugin."}))
    assert loop_verify["loop"]["lastVerification"]["status"] == "PASS"
    loop_inbox = json.loads(provider.handle_tool_call("total_recall_loop_inbox", {"agent": "sparky"}))
    assert loop_inbox["count"] == 1
    assert loop_inbox["loops"][0]["loop_id"] == loop_id
    loop_complete = json.loads(provider.handle_tool_call("total_recall_loop_complete", {"loop_id": loop_id, "summary": "Done."}))
    assert loop_complete["loop"]["status"] == "completed"
    assert json.loads(provider.handle_tool_call("total_recall_loop_inbox", {"agent": "sparky"}))["count"] == 0


def _install_fake_config_api(monkeypatch, config_path, save):
    package = types.ModuleType("hermes_cli")
    config = types.ModuleType("hermes_cli.config")
    config.get_config_path = lambda: config_path
    config.save_config = save
    monkeypatch.setitem(sys.modules, "hermes_cli", package)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config)


def test_plugin_save_config_delegates_profile_local_memory_settings(tmp_path, monkeypatch):
    module = _load_plugin(monkeypatch)
    provider = module.TotalRecallMemoryProvider()
    calls = []
    _install_fake_config_api(monkeypatch, tmp_path / "config.yaml", lambda payload, **kwargs: calls.append((payload, kwargs)))
    provider.save_config(
        {
            "home": str(tmp_path / "custom-recall"),
            "auto_rehydrate.enabled": "false",
            "auto_rehydrate.context_threshold": "0.82",
        },
        str(tmp_path),
    )

    assert calls == [(
        {"memory": {
            "provider": "total-recall",
            "total-recall": {
                "home": str(tmp_path / "custom-recall"),
                "auto_rehydrate": {"enabled": False, "context_threshold": 0.82},
            },
        }},
        {"merge_existing": True},
    )]


def test_plugin_save_config_without_pyyaml_never_replaces_existing_yaml(tmp_path, monkeypatch):
    module = _load_plugin(monkeypatch)
    path = tmp_path / "config.yaml"
    original = "model:\n  default: example/model\nplugins:\n  enabled: [other-plugin]\nmemory:\n  total-recall:\n    auto_rehydrate:\n      cooldown_seconds: 300\n"
    path.write_text(original, encoding="utf-8")
    calls = []
    _install_fake_config_api(monkeypatch, path, lambda payload, **kwargs: calls.append((payload, kwargs)))
    monkeypatch.setitem(sys.modules, "yaml", None)

    module.TotalRecallMemoryProvider().save_config({"home": "custom-recall"}, str(tmp_path))

    # This is a host-API contract test: Hermes owns the actual merge/write.
    # The provider passes a partial update with merge_existing, not a stale full config.
    assert calls == [(
        {"memory": {"provider": "total-recall", "total-recall": {"home": "custom-recall"}}},
        {"merge_existing": True},
    )]
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("error", [ValueError("unreadable config"), ImportError("missing YAML backend")])
def test_plugin_save_config_propagates_host_error_without_fallback(tmp_path, monkeypatch, error):
    module = _load_plugin(monkeypatch)
    path = tmp_path / "config.yaml"
    original = "model:\n  default: example/model\n"
    path.write_text(original, encoding="utf-8")

    def fail_save(payload, *, merge_existing):
        assert merge_existing is True
        raise error

    _install_fake_config_api(monkeypatch, path, fail_save)
    with pytest.raises(type(error), match=str(error)):
        module.TotalRecallMemoryProvider().save_config({"home": "custom-recall"}, str(tmp_path))
    assert path.read_text(encoding="utf-8") == original


def test_plugin_save_config_refuses_other_profile(tmp_path, monkeypatch):
    module = _load_plugin(monkeypatch)
    calls = []
    _install_fake_config_api(monkeypatch, tmp_path / "active" / "config.yaml", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(ValueError, match="different Hermes profile"):
        module.TotalRecallMemoryProvider().save_config({"home": "custom-recall"}, str(tmp_path / "other"))

    assert calls == []
    assert not (tmp_path / "other").exists()


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


def test_plugin_auto_rehydrate_verifies_once(tmp_path, monkeypatch):
    from total_recall_core import TotalRecallCore

    module = _load_plugin(monkeypatch)
    provider = module.TotalRecallMemoryProvider()
    provider.initialize("s1", hermes_home=str(tmp_path))
    provider.sync_turn("bounded rehydrate marker", "stored", session_id="s1")
    provider.handle_tool_call("total_recall_checkpoint", {"session_id": "s1"})
    original_verify = TotalRecallCore.verify
    verifications = []

    def counted_verify(core, **kwargs):
        verifications.append(kwargs)
        return original_verify(core, **kwargs)

    monkeypatch.setattr(TotalRecallCore, "verify", counted_verify)
    provider.on_turn_start(1, "continue", context_usage_ratio=0.8)
    auto = provider.prefetch("bounded rehydrate marker", session_id="s1")

    assert "status: PASS" in auto
    assert "bounded rehydrate marker" in auto
    assert len(verifications) == 1


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


def test_plugin_queues_writes_when_remote_lease_held_by_other_device(tmp_path, monkeypatch):
    from total_recall_core import TotalRecallConfig, TotalRecallCore

    module = _load_plugin(monkeypatch)
    remote = tmp_path / "remote"
    owner = TotalRecallCore(TotalRecallConfig(home=tmp_path / "owner", enable_lancedb=False, enable_qmd=False))
    monkeypatch.setenv("TOTAL_RECALL_BACKUP_PASSPHRASE", "lease-passphrase")
    owner.ingest(kind="note", text="Lease holder memory.", session_id="lease")
    owner.backup_push(target=str(remote))
    owner.lease_acquire(target=str(remote), ttl_seconds=3600)
    monkeypatch.setenv("TOTAL_RECALL_REMOTE_TARGET", str(remote))

    provider = module.TotalRecallMemoryProvider()
    provider.initialize("s1", hermes_home=str(tmp_path / "hermes"))
    provider.sync_turn("queued because lease is held", "not written", session_id="s1")

    pending = tmp_path / "hermes" / "total-recall" / "state" / "pending_events.jsonl"
    assert pending.exists()
    queued = json.loads(pending.read_text(encoding="utf-8").splitlines()[-1])
    assert queued["kind"] == "turn"
    assert "queued because lease is held" in queued["text"]
    assert TotalRecallCore(TotalRecallConfig(home=tmp_path / "hermes" / "total-recall", enable_lancedb=False, enable_qmd=False)).health()["eventCount"] == 0

    warning = provider.prefetch("anything", session_id="s1")
    assert "status: READ_ONLY" in warning
