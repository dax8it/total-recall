"""Conservative recovery: preserve records, never invent completed turns."""
import importlib.util
from pathlib import Path

from total_recall_core import TotalRecallConfig, TotalRecallCore


def recovery_module():
    path = Path(__file__).parents[1] / "scripts" / "recover_hermes_session_gap.py"
    spec = importlib.util.spec_from_file_location("session_gap_recovery", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(mid, role, content, **kwargs):
    return {"id": mid, "session_id": "session", "role": role, "content": content,
            "timestamp": 1000 + mid, "tool_calls": None, "_compressed_summary": 0,
            "display_kind": None, "finish_reason": "stop" if role == "assistant" else None,
            **kwargs}


def test_plan_omits_captured_turns_and_generated_scaffolding():
    recovery = recovery_module()
    records = [row(1, "user", "already captured"), row(2, "assistant", "stored"),
               row(3, "user", "actual missing user\u2028text"), row(4, "assistant", "actual response"),
               row(5, "user", "generated", _compressed_summary=1),
               row(6, "user", "worker notification", display_kind="async_delegation_complete"),
               row(7, "assistant", "unfinished", finish_reason="incomplete"),
               row(8, "assistant", "tool call", tool_calls="[]", finish_reason="tool_calls")]
    events = [{"session_id": "session", "kind": "turn", "text": "User: already captured\nAssistant: stored"}]
    plan = recovery.plan_records(records, events, "source")
    assert [r["id"] for r in plan["records"]] == [3, 4]
    assert plan["counts"] == {"source": 8, "excluded": 4, "covered": 2, "pending": 2}
    assert plan["records"][0]["content"] == records[2]["content"]


def test_recovery_append_preserves_history_and_replay_is_noop(tmp_path):
    recovery = recovery_module()
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_qmd=False, enable_lancedb=False))
    core.sync_turn("original", "response", session_id="session")
    before = core.ledger_file.read_bytes()
    records = [row(3, "user", "missing\u2028message"), row(4, "assistant", "recorded response")]
    first = recovery.apply_records(core, records, "source", "snapshot-hash")
    assert first["recovered_records"] == 2
    assert first["appended_events"] == 1
    assert core.ledger_file.read_bytes().startswith(before)
    events = core._read_events(verify_chain=True)
    recovered = events[-1]
    assert recovered["source"] == "hermes.session_recovery"
    assert recovered["metadata"]["source_snapshot_sha256"] == "snapshot-hash"
    assert recovered["metadata"]["source_message_ids"] == [3, 4]
    assert recovered["kind"] == "recovered_session_records"
    after = core.ledger_file.read_bytes()
    second = recovery.apply_records(core, records, "source", "snapshot-hash")
    assert second["appended_events"] == 0
    assert second["recovered_records"] == 0
    assert core.ledger_file.read_bytes() == after
    assert core.reduce_state()["event_count"] == 2
