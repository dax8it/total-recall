from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
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


def test_verify_allows_stale_checkpoint_against_signed_ledger_prefix(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Checkpointed memory.", session_id="s1")
    core.checkpoint(session_id="s1")
    core.ingest(kind="note", text="Newer uncheckpointed memory.", session_id="s1")

    verified = core.verify(session_id="s1")
    assert verified["ok"] is True
    assert "checkpoint_stale" in verified["warnings"]
    assert verified["checkpointLagEvents"] == 1


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


def test_documents_ingest_file_search_and_knowledge_query(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    strategy = docs / "brand-strategy.md"
    strategy.write_text(
        "# Brand Strategy\n\nThe storefront promise is ten-day returns with tokenized checkout trust.",
        encoding="utf-8",
    )
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))

    ingested = core.ingest_documents([strategy], session_id="brand-docs", scope="public")

    assert ingested["ok"] is True
    assert ingested["ingestedFiles"] == 1
    assert ingested["chunkCount"] == 1
    event = ingested["events"][0]
    assert event["kind"] == "document"
    assert event["scope"] == "public"
    assert event["metadata"]["file_name"] == "brand-strategy.md"
    assert "Document:" in event["text"]

    search = core.search("tokenized checkout", session_id="brand-docs")
    assert search["ok"] is True
    assert search["results"]
    assert "tokenized checkout trust" in search["results"][0]["text"]

    answer = core.knowledge_query("What is the storefront promise?", allowed_scopes=["public"])
    assert answer["ok"] is True
    assert answer["citations"]
    assert any("ten-day returns" in item["text"] for item in answer["evidence"])


def test_documents_ingest_folder_skips_unsupported_and_chunks(tmp_path):
    folder = tmp_path / "drop"
    folder.mkdir()
    (folder / "readme.md").write_text("Project Alpha launch notes.", encoding="utf-8")
    (folder / "long.txt").write_text(("Paragraph about fulfillment.\n\n" * 120), encoding="utf-8")
    (folder / "image.png").write_bytes(b"\x89PNG\x00binary")
    (folder / ".hidden.md").write_text("Hidden note should be skipped.", encoding="utf-8")
    nested = folder / "nested"
    nested.mkdir()
    (nested / "ops.yaml").write_text("promise: weekly inventory review", encoding="utf-8")
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))

    ingested = core.ingest_documents([folder], scope="internal", chunk_chars=1000)

    assert ingested["ok"] is True
    assert ingested["ingestedFiles"] == 3
    assert ingested["chunkCount"] > 3
    skipped = {Path(item["path"]).name: item["reason"] for item in ingested["files"] if item["status"] == "skipped"}
    assert skipped["image.png"] == "unsupported_extension"
    assert skipped[".hidden.md"] == "ignored_path"
    assert core.search("weekly inventory review")["count"] >= 1


def test_documents_ingest_dry_run_and_size_limit_do_not_write_ledger(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    small = folder / "small.txt"
    small.write_text("Small trusted note.", encoding="utf-8")
    large = folder / "large.txt"
    large.write_text("x" * 200, encoding="utf-8")
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))

    dry_run = core.ingest_documents([folder], dry_run=True, max_file_bytes=100)

    assert dry_run["ok"] is True
    assert dry_run["status"] == "DRY_RUN"
    assert dry_run["chunkCount"] == 1
    skipped = {Path(item["path"]).name: item["reason"] for item in dry_run["files"] if item["status"] == "skipped"}
    assert skipped["large.txt"] == "file_too_large"
    assert core.health()["eventCount"] == 0


def test_documents_ingest_cli_text_output(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "cli.md").write_text("CLI document marker for onboarding.", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "total_recall_core.cli",
            "--home",
            str(tmp_path / "store"),
            "documents",
            "ingest",
            str(docs),
            "--format",
            "text",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert "Total Recall document ingest: ready" in result.stdout
    assert "Files ingested: 1" in result.stdout

    search = subprocess.run(
        [
            sys.executable,
            "-m",
            "total_recall_core.cli",
            "--home",
            str(tmp_path / "store"),
            "search",
            "CLI document marker",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert search.returncode == 0
    assert "CLI document marker" in search.stdout


def test_obsidian_vault_export_generates_wikilinked_projection(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    strategy = docs / "brand-strategy.md"
    strategy.write_text(
        "# Brand Strategy\n\nThe Storefront Alpha promise is ten-day returns with tokenized checkout trust.",
        encoding="utf-8",
    )
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))
    core.ingest_documents([strategy], session_id="brand-docs", scope="public")
    core.ingest(
        kind="decision",
        text="Decision: Storefront Alpha should promise seven-day fulfillment only when ops/inventory.md confirms stock. This supersedes same-day delivery.",
        session_id="brand",
        scope="internal",
    )

    vault = tmp_path / "Total Recall Vault"
    exported = core.export_obsidian_vault(vault)

    assert exported["ok"] is True
    assert exported["schema"] == "total-recall-obsidian-vault-v1"
    assert exported["documentCount"] == 1
    assert exported["entityCount"] > 0
    assert (vault / "Index.md").exists()
    assert (vault / "Graph Legend.md").exists()
    assert (vault / "Compiled Truth.md").exists()
    manifest = json.loads((vault / ".total-recall-vault.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "total-recall-obsidian-vault-v1"
    assert manifest["authority"] == "ledger/checkpoints/anchors"
    assert manifest["import_status"] == "selected_edit_preview_and_promote_available"

    index_text = (vault / "Index.md").read_text(encoding="utf-8")
    assert "[[Compiled Truth]]" in index_text
    assert "vault import-preview" in index_text
    source_pages = sorted((vault / "Sources").glob("*.md"))
    document_pages = sorted((vault / "Documents").glob("*.md"))
    entity_pages = sorted((vault / "Entities").glob("*.md"))
    decision_pages = sorted((vault / "Decisions").glob("*.md"))
    timeline_pages = sorted((vault / "Timeline").glob("*.md"))
    assert source_pages
    assert document_pages
    assert entity_pages
    assert decision_pages
    assert timeline_pages
    assert "ledger:" in source_pages[0].read_text(encoding="utf-8")
    assert "Evidence hash" in source_pages[0].read_text(encoding="utf-8")
    assert "[[Sources/" in document_pages[0].read_text(encoding="utf-8")
    assert "[[" in entity_pages[0].read_text(encoding="utf-8")

    blocked = core.export_obsidian_vault(vault)
    assert blocked["ok"] is False
    assert blocked["status"] == "EXISTS"

    stale = vault / "stale.txt"
    stale.write_text("remove me", encoding="utf-8")
    forced = core.export_obsidian_vault(vault, force=True, allowed_scopes=["public", "internal"])
    assert forced["ok"] is True
    assert not stale.exists()
    assert (vault / "Index.md").exists()

    protected = core.export_obsidian_vault(core.home, force=True)
    assert protected["ok"] is False
    assert protected["error"] == "vault_output_inside_total_recall_home"
    assert core.ledger_file.exists()


def test_obsidian_vault_export_cli_text_output(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "cli-vault.md").write_text("CLI vault marker for Obsidian export.", encoding="utf-8")
    home = tmp_path / "store"
    core = TotalRecallCore(TotalRecallConfig(home=home, enable_lancedb=False, enable_qmd=False))
    core.ingest_documents([docs], session_id="docs", scope="public")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "total_recall_core.cli",
            "--home",
            str(home),
            "vault",
            "export",
            "--out",
            str(tmp_path / "vault"),
            "--format",
            "text",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert "Total Recall vault export: ready" in result.stdout
    assert "Authority: Total Recall ledger/checkpoints/anchors" in result.stdout
    assert (tmp_path / "vault" / "Index.md").exists()

    alias = subprocess.run(
        [
            sys.executable,
            "-m",
            "total_recall_core.cli",
            "--home",
            str(home),
            "obsidian",
            "export",
            "--out",
            str(tmp_path / "obsidian-vault"),
            "--format",
            "json",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert alias.returncode == 0
    assert json.loads(alias.stdout)["schema"] == "total-recall-obsidian-vault-v1"


def test_working_context_source_ingest_freshness_and_temporal_timeline(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))

    first = core.ingest_source(
        source_type="meeting",
        title="January Promise Review",
        text="Decision: Brand promise is same-day delivery.",
        occurred_at="2026-01-10T10:00:00Z",
        scope="public",
        metadata={"freshness_category": "promise"},
    )
    second = core.ingest_source(
        source_type="slack",
        title="February Promise Update",
        text="Decision: Brand promise is seven-day fulfillment. This supersedes old same-day promise.",
        occurred_at="2026-02-10T10:00:00Z",
        scope="public",
        metadata={"freshness_category": "promise"},
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["event"]["kind"] == "source_meeting"
    assert second["event"]["metadata"]["occurred_at"] == "2026-02-10T10:00:00Z"

    report = core.knowledge_freshness_report(
        entity="brand promise",
        category="promise",
        at_time="2026-03-01T00:00:00Z",
        allowed_scopes=["public"],
    )
    assert report["ok"] is True
    assert report["counts"]["current"] == 1
    assert report["counts"]["superseded"] == 1
    current = [item for item in report["items"] if item["freshness"] == "current"][0]
    superseded = [item for item in report["items"] if item["freshness"] == "superseded"][0]
    assert current["subject"] == "Brand promise"
    assert "seven-day fulfillment" in current["text"]
    assert "same-day delivery" in superseded["text"]

    query = core.knowledge_query("brand promise", mode="explore", allowed_scopes=["public"])
    assert "freshness_attention_required" in query["warnings"]

    timeline = core.knowledge_graph_timeline(
        "brand promise",
        at_time="2026-01-20T00:00:00Z",
        allowed_scopes=["public"],
    )
    assert timeline["ok"] is True
    assert any("same-day delivery" in item["text"] for item in timeline["asOf"])
    assert any("seven-day fulfillment" in item["text"] for item in timeline["afterAsOf"])
    assert timeline["timeline"][0]["timestamp"] == "2026-01-10T10:00:00Z"


def test_source_ingest_and_freshness_cli_text_output(tmp_path):
    home = tmp_path / "store"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    ingested = subprocess.run(
        [
            sys.executable,
            "-m",
            "total_recall_core.cli",
            "--home",
            str(home),
            "sources",
            "ingest",
            "--type",
            "meeting",
            "--title",
            "Renewal Policy Review",
            "--occurred-at",
            "2026-01-05T12:00:00Z",
            "--scope",
            "public",
            "--text",
            "Decision: Renewal policy is month-to-month.",
            "--format",
            "text",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert ingested.returncode == 0
    assert "Total Recall source ingest: ingested" in ingested.stdout
    assert "source_meeting" in (home / "ledger" / "events.jsonl").read_text(encoding="utf-8")

    freshness = subprocess.run(
        [
            sys.executable,
            "-m",
            "total_recall_core.cli",
            "--home",
            str(home),
            "knowledge",
            "freshness",
            "--category",
            "policy",
            "--format",
            "text",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert freshness.returncode == 0
    assert "Total Recall freshness report" in freshness.stdout
    assert "Renewal policy" in freshness.stdout


def test_obsidian_import_preview_and_promote_are_explicit(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Original project memory.", session_id="s1", scope="public")
    vault = tmp_path / "vault"
    exported = core.export_obsidian_vault(vault)
    assert exported["ok"] is True

    edited = vault / "Edited Promise.md"
    edited.write_text(
        "---\ntype: \"edited_note\"\n---\n# Edited Promise\n\nDecision: Storefront promise is seven-day fulfillment after owner review.\n",
        encoding="utf-8",
    )
    before_count = core.health()["eventCount"]

    preview = core.vault_import_preview(vault, notes=["Edited Promise.md"], session_id="review", scope="internal")

    assert preview["ok"] is True
    assert preview["status"] == "PREVIEW"
    assert preview["proposalCount"] == 1
    assert core.health()["eventCount"] == before_count
    preview_path = core.home / "reviews" / "obsidian" / f"{preview['preview_id']}.json"
    assert preview_path.exists()

    promoted = core.vault_import_promote(preview["preview_id"])

    assert promoted["ok"] is True
    assert promoted["eventCount"] == 1
    assert promoted["events"][0]["kind"] == "obsidian_note_import"
    assert "seven-day fulfillment" in promoted["events"][0]["text"]
    assert (core.home / "reviews" / "obsidian" / "promoted" / f"{preview['preview_id']}.json").exists()


def test_named_federation_registry_and_query(tmp_path):
    main_home = tmp_path / "main"
    agent_home = tmp_path / "agent"
    core = TotalRecallCore(TotalRecallConfig(home=main_home, enable_lancedb=False, enable_qmd=False))
    agent = TotalRecallCore(TotalRecallConfig(home=agent_home, enable_lancedb=False, enable_qmd=False))
    agent.ingest(kind="note", text="Agent beta knows the support promise is thirty-day returns.", session_id="agent", scope="public")
    agent.knowledge_index_rebuild()

    registered = core.federation_register("agent-beta", agent_home, role="hermes-agent", scopes=["public"])
    assert registered["ok"] is True
    assert registered["target"]["home_hash"]
    assert core.federation_list()["targets"][0]["name"] == "agent-beta"

    blocked = core.federation_query("support promise", targets=["agent-beta"], allowed_scopes=["public"])
    assert blocked["federation"]["status"] == "AUTHORIZATION_REQUIRED"
    assert blocked["registry"]["targets"][0]["name"] == "agent-beta"

    allowed = core.federation_query(
        "support promise",
        targets=["agent-beta"],
        authorize=True,
        allowed_scopes=["public"],
    )
    assert allowed["federation"]["authorized"] is True
    assert allowed["federation"]["merged"] is False
    assert allowed["federation"]["workspaces"][0]["citations"]

    removed = core.federation_remove("agent-beta")
    assert removed["ok"] is True
    assert core.federation_list()["targets"] == []


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


def test_generated_reports_are_not_retrieval_sources(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    report = tmp_path / "reports" / "rehydrate_self_reference.json"
    report.write_text(
        json.dumps(
            {
                "schema": "total-recall-report-v1",
                "text": "Self-referential report phrase should stay out of retrieval.",
            }
        ),
        encoding="utf-8",
    )

    search = core.search("Self-referential report phrase")

    assert search["ok"] is True
    assert all("Self-referential report phrase" not in result["text"] for result in search["results"])
    assert all(not str(result["source_ref"]).startswith(str(tmp_path / "reports")) for result in search["results"])


def test_knowledge_engine_query_graph_synthesis_and_eval(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    first = core.ingest(
        kind="decision",
        text="Decision: EDEVA should promise seven-day fulfillment only when /ops/inventory.md confirms stock. This supersedes the old same-day promise.",
        session_id="brand",
        scope="internal",
    )
    core.ingest(
        kind="note",
        text="Payment trust note: Stripe checkout uses tokenized payments; do not store card numbers or API key sk-example-secret-value in memory.",
        session_id="brand",
        scope="internal",
    )

    rebuilt = core.knowledge_index_rebuild()
    assert rebuilt["ok"] is True
    assert rebuilt["rebuilt"]["sources"] == 2
    assert rebuilt["rebuilt"]["redactions"] >= 1

    graph = core.knowledge_graph_status()
    assert graph["ok"] is True
    assert graph["entityCount"] > 0
    assert graph["edgeCount"] > 0
    assert graph["uncitedActiveItems"] == 0

    truth_status = core.knowledge_compiled_truth_status()
    assert truth_status["status"] == "PASS"
    assert truth_status["fresh"] is True
    truth = core.knowledge_compiled_truth_show(format_="md")
    assert truth["ok"] is True
    assert "# Total Recall Compiled Truth" in truth["text"]
    assert "seven-day fulfillment" in truth["text"]
    assert "ledger:" in truth["text"]

    inspected = core.knowledge_graph_inspect(entity="fulfillment", allowed_scopes=["internal"])
    assert inspected["ok"] is True
    assert inspected["entities"]
    assert inspected["edges"]
    assert inspected["citations"]

    traversed = core.knowledge_graph_traverse("fulfillment", depth=2, allowed_scopes=["internal"])
    assert traversed["ok"] is True
    assert traversed["start"]
    assert traversed["edges"]
    assert traversed["citations"]

    query = core.knowledge_query(
        "which fulfillment promise can the brand keep?",
        mode="strict",
        session_id="brand",
        allowed_scopes=["internal"],
    )
    assert query["ok"] is True
    assert query["status"] == "PASS"
    assert query["citations"]
    assert query["graph"]["entities"]
    assert "seven-day fulfillment" in query["answer"]
    assert Path(query["providerReport"]["path"]).exists()

    before_memory = core.knowledge_query(
        "fulfillment promise",
        mode="explore",
        session_id="brand",
        at_time="2000-01-01T00:00:00Z",
        allowed_scopes=["internal"],
    )
    assert before_memory["status"] == "PASS"
    assert before_memory["temporal"]["applied"] is True
    assert before_memory["citations"] == []

    synth = core.knowledge_synthesize_run()
    assert synth["ok"] is True
    assert synth["proposals"]
    assert Path(synth["runDir"]).exists()

    promoted = core.knowledge_synthesize_promote(synth["proposals"][0]["proposal_id"], session_id="brand")
    assert promoted["ok"] is True
    assert promoted["event"]["kind"] == "knowledge_synthesis_promoted"

    evaluated = core.knowledge_evaluate_run()
    assert evaluated["ok"] is True
    assert evaluated["score"] >= 7
    check_names = {check["name"] for check in evaluated["checks"]}
    assert "compiled_truth_projection_fresh" in check_names
    assert "graph_inspect_traverse" in check_names
    assert "fixture_external_provider_auth_gate" in check_names
    assert "fixture_redacted_hermes_smoke" in check_names
    assert "fixture_federation_workspace_separated" in check_names
    scorecard = core.knowledge_evaluate_scorecard()
    assert scorecard["ok"] is True


def test_knowledge_engine_does_not_index_generated_reports(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path, enable_lancedb=False, enable_qmd=False))
    report = tmp_path / "reports" / "rehydrate_feedback.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"text": "Feedback-loop phrase must stay fenced."}), encoding="utf-8")
    core.ingest(kind="note", text="Normal cited memory.", session_id="s1")

    core.knowledge_index_rebuild()
    query = core.knowledge_query("Feedback-loop phrase", mode="explore")

    assert query["ok"] is True
    assert query["citations"] == []
    assert all("Feedback-loop phrase" not in item["text"] for item in query["evidence"])


def test_knowledge_engine_provider_reports_and_explicit_federation(tmp_path):
    main_home = tmp_path / "main"
    fed_home = tmp_path / "federated"
    core = TotalRecallCore(TotalRecallConfig(home=main_home, enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Local public storefront promise is ten day returns.", session_id="s1", scope="public")
    core.ingest(kind="note", text="Payment processor secret sk-report-secret-value must stay private.", session_id="s1", scope="private")
    core.knowledge_index_rebuild()

    private_query = core.knowledge_query("payment processor secret", mode="explore", allowed_scopes=["private"])
    report_path = Path(private_query["providerReport"]["path"])
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["providerCalls"][0]["provider"] == "local-hash-rerank"
    assert report["providerCalls"][0]["redactionCount"] >= 1
    assert "sk-report-secret-value" not in report_text
    assert "Payment processor secret" not in report_text

    external_blocked = core.knowledge_query(
        "payment processor secret",
        mode="explore",
        allowed_scopes=["private", "public"],
        external_providers=["hindsight"],
    )
    assert external_blocked["providerCalls"][-1]["provider"] == "external:hindsight"
    assert external_blocked["providerCalls"][-1]["status"] == "SKIPPED"
    assert external_blocked["providerCalls"][-1]["scopesSent"] == []
    assert "external_provider_requires_explicit_authorization" in external_blocked["warnings"]

    external_authorized = core.knowledge_query(
        "payment processor secret",
        mode="explore",
        allowed_scopes=["private", "public"],
        external_providers=["hindsight"],
        external_provider_authorized=True,
    )
    assert external_authorized["providerCalls"][-1]["status"] == "UNAVAILABLE"
    assert "private" not in external_authorized["providerCalls"][-1]["scopesSent"]
    assert "external_provider_unavailable" in external_authorized["warnings"]

    federated = TotalRecallCore(TotalRecallConfig(home=fed_home, enable_lancedb=False, enable_qmd=False))
    federated.ingest(kind="note", text="Federated brand promise is thirty day returns.", session_id="s2", scope="public")
    federated.knowledge_index_rebuild()

    blocked = core.knowledge_query("federated brand promise", mode="explore", allowed_scopes=["public"], federate=[str(fed_home)])
    assert blocked["federation"]["status"] == "AUTHORIZATION_REQUIRED"
    assert blocked["federation"]["workspaces"] == []
    assert "federation_requires_explicit_authorization" in blocked["warnings"]

    authorized = core.knowledge_query(
        "federated brand promise",
        mode="explore",
        allowed_scopes=["public"],
        federate=[str(fed_home)],
        federation_authorized=True,
    )
    assert authorized["federation"]["authorized"] is True
    assert authorized["federation"]["merged"] is False
    assert authorized["federation"]["workspaces"][0]["ok"] is True
    assert authorized["federation"]["workspaces"][0]["citations"]
    auth_report = json.loads(Path(authorized["providerReport"]["path"]).read_text(encoding="utf-8"))
    assert auth_report["federation"]["requestedCount"] == 1
    assert auth_report["federation"]["workspaceStatuses"][0]["citationCount"] >= 1


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


def test_trust_gate_persists_hardcoded_execution_report(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))
    core.ingest_source(
        source_type="meeting",
        title="Trust Gate Launch Review",
        text="Decision: Trust gate launch promise is hard-coded verification.",
        occurred_at="2026-01-01T00:00:00Z",
        session_id="trust",
        scope="public",
        metadata={"freshness_category": "promise"},
    )
    core.checkpoint(session_id="trust")

    gate = core.trust_gate_run()

    assert gate["ok"] is True
    assert gate["status"] == "PASS"
    assert gate["schema"] == "total-recall-trust-gate-v1"
    assert Path(gate["report"]["json"]).exists()
    check_names = {check["name"] for check in gate["checks"]}
    assert {
        "real_store_ledger_hash_chain",
        "real_store_checkpoint_anchor_current",
        "real_store_core_index_rebuildable",
        "real_store_knowledge_authority",
        "real_store_export_import_round_trip",
        "fixture_source_ingest_ledgered",
        "fixture_freshness_supersession",
        "fixture_temporal_graph_timeline",
        "fixture_obsidian_preview_no_ledger_write",
        "fixture_obsidian_promote_ledgered",
        "fixture_federation_authorization_required",
        "fixture_federation_workspace_separated",
        "fixture_persistence_checkpoint_export_import",
        "fixture_hermes_plugin_bundle_surface",
    } <= check_names
    assert all(check["ok"] for check in gate["checks"] if check["name"].startswith("fixture_"))

    status = core.trust_gate_status()
    assert status["ok"] is True
    assert status["gate_id"] == gate["gate_id"]


def test_trust_gate_fails_closed_without_checkpoint(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))
    core.ingest(kind="note", text="Uncheckpointed memory must not pass release trust.", session_id="trust")

    gate = core.trust_gate_run(persist=False)

    assert gate["ok"] is False
    assert gate["status"] == "FAIL_CLOSED"
    assert "real_store_checkpoint_anchor_current" in gate["failedRequired"]
    checkpoint_check = [check for check in gate["checks"] if check["name"] == "real_store_checkpoint_anchor_current"][0]
    assert checkpoint_check["ok"] is False


def test_trust_gate_cli_text_output(tmp_path):
    home = tmp_path / "cli-store"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    ingested = subprocess.run(
        [
            sys.executable,
            "-m",
            "total_recall_core.cli",
            "--home",
            str(home),
            "ingest",
            "--kind",
            "note",
            "--text",
            "CLI trust gate memory.",
            "--session-id",
            "cli",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert ingested.returncode == 0

    checkpoint = subprocess.run(
        [
            sys.executable,
            "-m",
            "total_recall_core.cli",
            "--home",
            str(home),
            "checkpoint",
            "--session-id",
            "cli",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    assert checkpoint.returncode == 0

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "total_recall_core.cli",
            "--home",
            str(home),
            "trust",
            "verify",
            "--format",
            "text",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0
    assert "Total Recall trust gate: pass" in result.stdout
    assert "failed required: 0" in result.stdout


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


def test_backup_run_exports_verifies_and_prunes_old_backups(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))
    backup_dir = tmp_path / "backups"
    core.ingest(kind="note", text="Retained backup memory.", session_id="s1")
    core.checkpoint(session_id="s1")
    core.ingest(kind="note", text="New memory before backup.", session_id="s1")

    first = core.backup_run(str(backup_dir), keep=1)
    assert first["ok"] is True
    assert first["checkpoint"]["ok"] is True
    assert first["doctor"]["ok"] is True
    assert first["verification"]["ok"] is True
    assert first["backupStatus"]["count"] == 1
    assert Path(first["backup"]["bundle"]).exists()

    # Ensure the second backup receives a distinct timestamp.
    old_path = Path(first["backup"]["bundle"])
    old_path.rename(backup_dir / "total-recall-backup-20000101-000000.tar.gz")
    os.utime(backup_dir / "total-recall-backup-20000101-000000.tar.gz", (946684800, 946684800))

    second = core.backup_run(str(backup_dir), keep=1)
    assert second["ok"] is True
    assert second["backupStatus"]["count"] == 1
    assert len(second["retention"]["pruned"]) == 1
    assert not (backup_dir / "total-recall-backup-20000101-000000.tar.gz").exists()


def test_sync_status_compares_current_local_state_to_latest_archive(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "store", enable_lancedb=False, enable_qmd=False))
    backup_dir = tmp_path / "backups"
    core.ingest(kind="note", text="Synced memory.", session_id="s1")

    backup = core.backup_run(str(backup_dir), keep=10)
    assert backup["ok"] is True
    synced = core.sync_status(str(backup_dir))
    assert synced["relation"] == "in_sync"
    assert synced["local"]["eventCount"] == synced["archive"]["latestCheckpoint"]["event_count"]

    core.ingest(kind="note", text="Local-only memory after latest archive.", session_id="s1")
    ahead = core.sync_status(str(backup_dir))
    assert ahead["relation"] == "local_ahead"
    assert "ahead by 1 event" in ahead["message"]


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
