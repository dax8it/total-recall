from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from total_recall_core import TotalRecallConfig, TotalRecallCore
from total_recall_core.dashboard import _handler


@contextmanager
def _dashboard(core: TotalRecallCore, backup_dir):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(core=core, backup_dir=backup_dir, keep=3, keep_days=30))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get_json(url: str):
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict | None = None):
    request = Request(
        url,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def test_dashboard_remote_mcp_admin_routes(tmp_path):
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "home", enable_lancedb=False, enable_qmd=False))
    backup_dir = tmp_path / "backups"
    core.ingest(kind="note", text="Storefront promise depends on demand, trust, payments, and fulfillment.", session_id="admin", scope="public")
    core.checkpoint(session_id="admin")

    with _dashboard(core, backup_dir) as base_url:
        with urlopen(base_url + "/", timeout=10) as response:
            html = response.read().decode("utf-8")

        assert "Memory Control Center" in html
        assert "Memory is protected" in html
        assert "Fix All" in html
        assert "Save Restore Point" in html
        assert "It can be stale even when the restore point is current" in html
        assert "Run the quality/release gate" in html
        assert "Safety Check" in html
        assert "Search Catalog" in html
        assert "Memory Protection" in html
        assert "Save before long sessions; restore only after verify" in html
        assert "Ask Memory" in html
        assert "Connection Readiness" in html
        assert "Continue on another machine" in html
        assert "Hugging Face process" in html
        assert "Context Risk Zone" in html
        assert "Agent work inbox" in html
        assert "First-run checklist" in html
        assert "Click any ? mark to see what that selected item means" in html
        assert "data-help-title" in html
        assert "Obsidian Vault Export" in html
        assert "runVaultExport" in html
        assert "runSourceIngest" in html
        assert "runFreshness" in html
        assert "runVaultImportPreview" in html

        status = _get_json(base_url + "/api/status")
        assert status["ok"] is True
        assert "knowledge" in status
        assert "mcp" in status
        assert "backupReadiness" in status
        assert "hf" in status
        assert "portable" in status
        assert "loops" in status
        assert "contextRisk" in status
        assert "setupChecklist" in status
        assert status["hf"]["tokenValueVisible"] is False
        assert "hf_" not in json.dumps(status["hf"])
        assert status["portable"]["restoreDefaults"]["testHome"] == "~/total-recall-restored-test"
        assert status["loops"]["mode"] == "read-only-review"
        assert status["setupChecklist"]["items"]
        assert status["backupReadiness"]["status"] == "NO_BACKUP"
        assert "Backups protect recovery" in status["backupReadiness"]["compactionRule"]
        assert status["mcp"]["surface"] == "local-admin-http"
        hf_provider = next(provider for provider in status["providers"] if provider["id"] == "huggingface")
        assert hf_provider["status"] == "available encrypted"
        assert "Portable agent clone storage" in hf_provider["note"]
        assert status["policy"]["defaultVaultDir"].endswith("TotalRecallVault")

        hf_status = _get_json(base_url + "/api/hf/status")
        portable_status = _get_json(base_url + "/api/portable/status")
        loops_status = _get_json(base_url + "/api/loops/inbox")
        context_risk = _get_json(base_url + "/api/context-risk")
        setup_checklist = _get_json(base_url + "/api/setup/checklist")
        assert hf_status["schema"] == "total-recall-hf-status-v1"
        assert hf_status["tokenValueVisible"] is False
        assert portable_status["schema"] == "total-recall-portable-status-v1"
        assert loops_status["mode"] == "read-only-review"
        assert context_risk["schema"] == "total-recall-context-risk-v1"
        assert setup_checklist["schema"] == "total-recall-setup-checklist-v1"

        rebuilt = _post_json(base_url + "/api/knowledge/index/rebuild")
        assert rebuilt["ok"] is True
        assert rebuilt["index"]["fresh"] is True

        query = _post_json(
            base_url + "/api/knowledge/query",
            {"query": "storefront promise payments fulfillment", "mode": "explore", "scopes": ["public"]},
        )
        assert query["ok"] is True
        assert query["citations"]

        graph = _get_json(base_url + "/api/knowledge/graph/inspect?entity=promise&limit=10")
        assert graph["ok"] is True

        source = _post_json(
            base_url + "/api/sources/ingest",
            {
                "sourceType": "meeting",
                "title": "Dashboard Promise Review",
                "occurredAt": "2026-01-01T00:00:00Z",
                "scope": "public",
                "text": "Decision: Dashboard promise is cited review.",
            },
        )
        assert source["ok"] is True
        assert source["event"]["kind"] == "source_meeting"

        freshness = _get_json(base_url + "/api/knowledge/freshness?entity=dashboard%20promise&category=promise")
        assert freshness["ok"] is True
        assert freshness["items"]

        timeline = _get_json(base_url + "/api/knowledge/graph/timeline?entity=dashboard%20promise&atTime=2026-01-02T00%3A00%3A00Z")
        assert timeline["ok"] is True
        assert timeline["asOf"]

        truth = _get_json(base_url + "/api/knowledge/truth")
        assert truth["ok"] is True
        assert "Total Recall Compiled Truth" in truth["text"]

        vault = _post_json(
            base_url + "/api/vault/export",
            {"path": str(tmp_path / "vault"), "maxEvents": 100, "maxEntities": 50},
        )
        assert vault["ok"] is True
        assert vault["schema"] == "total-recall-obsidian-vault-v1"
        assert (tmp_path / "vault" / "Index.md").exists()
        assert (tmp_path / "vault" / ".total-recall-vault.json").exists()

        edited = tmp_path / "vault" / "Dashboard Edited.md"
        edited.write_text(
            "---\ntype: \"edited_note\"\n---\n# Dashboard Edited\n\nDecision: Dashboard reviewed import stays explicit.\n",
            encoding="utf-8",
        )
        preview = _post_json(
            base_url + "/api/vault/import-preview",
            {"vault": str(tmp_path / "vault"), "notes": ["Dashboard Edited.md"], "scope": "internal"},
        )
        assert preview["ok"] is True
        assert preview["proposalCount"] == 1
        promoted = _post_json(base_url + "/api/vault/import-promote", {"previewId": preview["preview_id"]})
        assert promoted["ok"] is True
        assert promoted["eventCount"] == 1

        fix_all = _post_json(base_url + "/api/protection/fix-all")
        assert fix_all["ok"] is True
        assert [step["name"] for step in fix_all["steps"]] == [
            "save_restore_point",
            "rebuild_search_catalog",
            "rebuild_graph",
            "build_compiled_truth",
            "run_release_gate",
            "write_latest_backup",
        ]
        assert fix_all["knowledge"]["index"]["fresh"] is True
        backup_status = _get_json(base_url + "/api/status")
        assert backup_status["backupReadiness"]["status"] == "CURRENT"
        assert backup_status["backupReadiness"]["relation"] == "in_sync"
        assert backup_status["backupReadiness"]["rehydrateReady"] is True
        trust = _post_json(base_url + "/api/trust/verify")
        assert trust["ok"] is True
        assert trust["summary"]["failedRequired"] == 0

        blocked = _post_json(base_url + "/api/vault/export", {"path": str(tmp_path / "vault")})
        assert blocked["ok"] is False
        assert blocked["status"] == "EXISTS"
