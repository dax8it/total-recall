from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

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
    assert checkpoint["anchor"]["signature"]

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
