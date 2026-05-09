from __future__ import annotations

import json
import sqlite3
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from total_recall_core import TotalRecallConfig, TotalRecallCore


def test_ingest_search_checkpoint_verify_rehydrate(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))

    ingested = core.ingest(
        kind="note",
        text="Total Recall smoke test memory.",
        session_id="smoke-session",
    )
    assert ingested["ok"] is True

    search = core.search("smoke test memory")
    assert search["ok"] is True
    assert search["results"]
    assert "smoke test memory" in search["results"][0]["text"]

    checkpoint = core.checkpoint(session_id="smoke-session")
    assert checkpoint["ok"] is True
    assert checkpoint["checkpoint"]["state_hash"]
    assert checkpoint["anchor"]["algorithm"] == "ed25519-local-v1"
    assert checkpoint["anchor"]["public_key"]
    assert checkpoint["anchor"]["signature"]
    public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(checkpoint["anchor"]["public_key"]))
    public_key.verify(
        bytes.fromhex(checkpoint["anchor"]["signature"]),
        checkpoint["checkpoint"]["checkpoint_hash"].encode("utf-8"),
    )

    verified = core.verify(session_id="smoke-session")
    assert verified["ok"] is True
    assert verified["status"] == "PASS"

    rehydrated = core.rehydrate(session_id="smoke-session", query="smoke test memory")
    assert rehydrated["ok"] is True
    assert "Total Recall Rehydrate Authority" in rehydrated["context_block"]
    assert "smoke test memory" in rehydrated["context_block"]


def test_verify_fails_closed_when_anchor_is_tampered(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Important continuity fact.", session_id="s1")
    checkpoint = core.checkpoint(session_id="s1")
    anchor_path = tmp_path / "anchors" / f"{checkpoint['checkpoint']['checkpoint_id']}.json"
    anchor = json.loads(anchor_path.read_text())
    anchor["signature"] = "bad"
    anchor_path.write_text(json.dumps(anchor))

    verified = core.verify(session_id="s1")
    assert verified["ok"] is False
    assert verified["status"] == "FAIL_CLOSED"
    assert "anchor_signature_mismatch" in verified["failures"]

    incidents = core.list_incidents(status="OPEN")
    assert incidents["count"] == 1
    assert incidents["incidents"][0]["severity"] == "FAIL_CLOSED"


def test_verify_fails_closed_when_ledger_text_is_tampered(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Original continuity fact.", session_id="s1")
    core.checkpoint(session_id="s1")
    lines = (tmp_path / "ledger" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["text"] = "Tampered continuity fact."
    lines[0] = json.dumps(event, sort_keys=True, separators=(",", ":"))
    (tmp_path / "ledger" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    verified = core.verify(session_id="s1")
    assert verified["ok"] is False
    assert verified["status"] == "FAIL_CLOSED"
    assert any("ledger_or_state_invalid" in failure for failure in verified["failures"])


def test_verify_fails_closed_when_ledger_event_is_deleted(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="First event.", session_id="s1")
    core.ingest(kind="note", text="Second event.", session_id="s1")
    core.checkpoint(session_id="s1")
    lines = (tmp_path / "ledger" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "ledger" / "events.jsonl").write_text(lines[1] + "\n", encoding="utf-8")

    verified = core.verify(session_id="s1")
    assert verified["ok"] is False
    assert any("ledger_or_state_invalid" in failure for failure in verified["failures"])


def test_verify_fails_closed_when_ledger_events_are_reordered(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="First event.", session_id="s1")
    core.ingest(kind="note", text="Second event.", session_id="s1")
    core.checkpoint(session_id="s1")
    lines = (tmp_path / "ledger" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "ledger" / "events.jsonl").write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")

    verified = core.verify(session_id="s1")
    assert verified["ok"] is False
    assert any("ledger_or_state_invalid" in failure for failure in verified["failures"])


def test_verify_fails_closed_when_checkpoint_is_modified(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Checkpoint protected memory.", session_id="s1")
    checkpoint = core.checkpoint(session_id="s1")
    checkpoint_path = Path(checkpoint["checkpointFile"])
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    payload["state_hash"] = "bad"
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

    verified = core.verify(session_id="s1")
    assert verified["ok"] is False
    assert "checkpoint_hash_mismatch" in verified["failures"]


def test_verify_fails_closed_when_anchor_is_missing(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Missing anchor memory.", session_id="s1")
    checkpoint = core.checkpoint(session_id="s1")
    anchor_path = tmp_path / "anchors" / f"{checkpoint['checkpoint']['checkpoint_id']}.json"
    anchor_path.unlink()

    verified = core.verify(session_id="s1")
    assert verified["ok"] is False
    assert "anchor_not_found" in verified["failures"]


def test_verify_fails_closed_when_anchor_checkpoint_hash_is_modified(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Anchor hash memory.", session_id="s1")
    checkpoint = core.checkpoint(session_id="s1")
    anchor_path = tmp_path / "anchors" / f"{checkpoint['checkpoint']['checkpoint_id']}.json"
    anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
    anchor["checkpoint_hash"] = "bad"
    anchor_path.write_text(json.dumps(anchor), encoding="utf-8")

    verified = core.verify(session_id="s1")
    assert verified["ok"] is False
    assert "anchor_checkpoint_hash_mismatch" in verified["failures"]
    assert "anchor_signature_mismatch" in verified["failures"]


def test_external_memory_quarantine_promote_reject(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    ext = core.external_ingest(text="External approved project context.", source="handoff.md")
    assert ext["ok"] is True
    assert core.external_list(queue="quarantine")["count"] == 1

    promoted = core.external_promote(ext["external"]["external_id"], session_id="s1")
    assert promoted["ok"] is True
    assert core.external_list(queue="quarantine")["count"] == 0
    assert core.external_list(queue="promoted")["count"] == 1
    assert core.search("approved project context")["count"] >= 1

    ext2 = core.external_ingest(text="Rejected context.", source="unknown")
    rejected = core.external_reject(ext2["external"]["external_id"], reason="untrusted")
    assert rejected["ok"] is True
    assert core.external_list(queue="rejected")["count"] == 1


def test_promoted_external_file_is_not_authority_without_ledger_event(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    promoted = tmp_path / "external-memory" / "promoted" / "fake.json"
    promoted.parent.mkdir(parents=True, exist_ok=True)
    promoted.write_text(json.dumps({"external_id": "fake", "text": "Unledgered promoted memory."}), encoding="utf-8")
    core.ingest(kind="note", text="Authoritative memory.", session_id="s1")
    core.checkpoint(session_id="s1")

    verified = core.verify(session_id="s1")
    assert verified["ok"] is True
    search = core.search("Unledgered promoted memory")
    assert all("Unledgered promoted memory" not in result["text"] for result in search["results"])
    assert all(result["source_ref"] != str(promoted) for result in search["results"])


def test_export_import_doctor_round_trip(tmp_path):
    source_home = tmp_path / "source"
    imported_home = tmp_path / "imported"
    bundle = tmp_path / "recall.tar.gz"
    core = TotalRecallCore(TotalRecallConfig(home=source_home, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Exported continuity memory.", session_id="s1")
    core.checkpoint(session_id="s1")

    doctor = core.doctor()
    assert doctor["ok"] is True
    exported = core.export_bundle(str(bundle))
    assert exported["ok"] is True
    assert bundle.exists()

    imported = TotalRecallCore(TotalRecallConfig(home=imported_home, enable_lancedb=False, enable_qmd=False))
    result = imported.import_bundle(str(bundle))
    assert result["ok"] is True
    assert imported.verify(session_id="s1")["ok"] is True
    assert imported.search("Exported continuity memory")["results"]


def test_import_rejects_unsafe_tar_paths(tmp_path):
    bundle = tmp_path / "unsafe.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        evil = tmp_path / "evil.txt"
        evil.write_text("bad", encoding="utf-8")
        tar.add(evil, arcname="../evil.txt")
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "target", enable_lancedb=False, enable_qmd=False))

    result = core.import_bundle(str(bundle))
    assert result["ok"] is False
    assert result["error"] == "unsafe_bundle_path"


def test_context_plan_has_citations(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="A cited recall block should mention StoryForge.", session_id="s1")
    plan = core.context_plan("StoryForge", session_id="s1")
    assert plan["ok"] is True
    assert "[Total Recall Context]" in plan["context"]
    assert "source:" in plan["context"]


def test_sqlite_fts_index_is_derived_and_searchable(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="SQLite FTS derived retrieval memory.", session_id="s1")

    status = core.index_status()
    assert status["ok"] is True
    sqlite_status = status["backends"]["sqlite-fts"]
    assert sqlite_status["backend"] == "sqlite-fts"
    assert sqlite_status["fresh"] is True
    assert sqlite_status["documentCount"] == 1

    search = core.search("derived retrieval")
    assert search["ok"] is True
    assert search["backend"] == "derived-hybrid"
    assert "sqlite-fts" in search["backends"]
    assert search["results"][0]["source_ref"].startswith("ledger:")


def test_verify_rebuilds_tampered_index_from_ledger(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Tampered derived index should be rebuilt.", session_id="s1")
    core.checkpoint(session_id="s1")

    with sqlite3.connect(tmp_path / "index" / "total_recall.sqlite") as conn:
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM documents_fts")
        conn.execute("UPDATE index_meta SET value = '0' WHERE key = 'event_count'")
        conn.commit()

    stale = core.index_status()
    assert stale["fresh"] is False
    assert stale["backends"]["sqlite-fts"]["documentCount"] == 0

    verified = core.verify(session_id="s1")
    assert verified["ok"] is True
    assert verified["indexRebuild"]["index"]["fresh"] is True
    assert verified["indexRebuild"]["index"]["backends"]["sqlite-fts"]["documentCount"] == 1

    search = core.search("tampered rebuilt")
    assert search["backend"] == "derived-hybrid"
    assert "sqlite-fts" in search["backends"]
    assert search["results"]


def test_parallel_health_and_search_do_not_collide_on_state_tempfile(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Parallel state writes should not collide.", session_id="s1")

    def run_health():
        return core.health()

    def run_search():
        return core.search("parallel state writes")

    with ThreadPoolExecutor(max_workers=2) as pool:
        health_result = pool.submit(run_health)
        search_result = pool.submit(run_search)

    assert health_result.result()["ok"] is True
    result = search_result.result()
    assert result["ok"] is True
    assert "indexErrors" not in result


def test_qmd_adapter_is_optional_derived_index(tmp_path):
    fake_qmd = tmp_path / "fake-qmd"
    fake_qmd.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:2] == ["--index", args[1] if len(args) > 1 else ""]:
    args = args[2:]
if "collection" in args and "add" in args:
    sys.exit(0)
if "collection" in args and "remove" in args:
    sys.exit(0)
if "search" in args:
    docs = Path(__file__).parent / "store" / "index" / "qmd-docs" / "events"
    rows = []
    for path in sorted(docs.glob("*.md")):
        text = path.read_text()
        if "adapter" in text.lower():
            rows.append({"file": f"qmd://total-recall/events/{path.name}", "score": 9.0, "snippet": text[:120]})
    print(json.dumps(rows))
    sys.exit(0)
sys.exit(0)
""",
        encoding="utf-8",
    )
    fake_qmd.chmod(0o755)
    home = tmp_path / "store"
    core = TotalRecallCore(
        TotalRecallConfig(home=home, enable_lancedb=False, enable_qmd=True, qmd_bin=str(fake_qmd))
    )
    core.ingest(kind="note", text="QMD adapter derived index memory.", session_id="s1")

    rebuilt = core.rebuild_index(backends=["qmd"])
    assert rebuilt["rebuilt"]["qmd"]["fresh"] is True
    search = core.search("adapter", session_id="s1")
    assert search["ok"] is True
    assert "qmd" in search["backends"]
    assert search["results"][0]["source_ref"].startswith("ledger:")


def test_lancedb_adapter_is_optional_derived_index(tmp_path):
    pytest.importorskip("lancedb")
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=True, enable_qmd=False))
    core.ingest(kind="note", text="LanceDB semantic derived vector memory.", session_id="s1")

    rebuilt = core.rebuild_index(backends=["lancedb"])
    assert rebuilt["rebuilt"]["lancedb"]["fresh"] is True
    search = core.search("semantic vector", session_id="s1")
    assert search["ok"] is True
    assert "lancedb" in search["backends"]
    assert search["results"][0]["source_ref"].startswith("ledger:")
