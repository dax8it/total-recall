from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from total_recall_core import TotalRecallConfig, TotalRecallCore
from total_recall_core.dashboard import _handler, _redact_payload, _redacted_line


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


def _post_json_allow_error(url: str, payload: dict | None = None):
    try:
        return _post_json(url, payload)
    except HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def _install_fake_hf(tmp_path, *, private: bool = True, leak: str = "FAKE_SECRET_VALUE", no_repo_info: bool = False, shebang: str = "#!/usr/bin/env python3"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    remote_dir = tmp_path / "fake-hf-remote"
    remote_dir.mkdir()
    hf = bin_dir / "hf"
    hf.write_text(
        f"{shebang}\n"
        "import json, os, pathlib, shutil, sys\n"
        f"leak={leak!r}\n"
        f"private={str(private)!r}\n"
        f"no_repo_info={str(no_repo_info)!r}\n"
        f"remote=pathlib.Path({str(remote_dir)!r})\n"
        "args=sys.argv[1:]\n"
        "if args[:2]==['auth','whoami']:\n print('fake-user'); raise SystemExit(0)\n"
        "if args[:2]==['repo','info']:\n\n if no_repo_info == 'True': print(\"Error: No such command 'info'.\", file=sys.stderr); raise SystemExit(2)\n print(json.dumps({'id':args[2], 'private': private == 'True'})); print('token='+leak, file=sys.stderr); raise SystemExit(0)\n"
        "if args[:2]==['repo','create']:\n print('created private dataset token='+leak); raise SystemExit(0)\n"
        "if args and args[0]=='upload':\n src=pathlib.Path(args[2]); dest=remote/args[3]; shutil.copy2(src, dest); print('uploaded token='+leak); raise SystemExit(0)\n"
        "if args and args[0]=='download':\n local=pathlib.Path(args[args.index('--local-dir')+1]); local.mkdir(parents=True, exist_ok=True);\n if os.environ.get('TOTAL_RECALL_FAKE_HF_DOWNLOAD_FAIL')=='1': print('download failed token='+leak, file=sys.stderr); raise SystemExit(7)\n [shutil.copy2(p, local/p.name) for p in remote.glob('total-recall-portable-clone-*.tar.gz.enc*')]; raise SystemExit(0)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    hf.chmod(0o755)
    return bin_dir


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
        assert "Agent Fleet" in html
        assert "Protected action ladder" in html
        assert "Cross-agent memory stays isolated unless explicitly authorized" in html
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
        assert status["contextRisk"]["schema"] == "total-recall-context-risk-v1"
        assert status["contextRisk"]["checkpointLagEvents"] == 0
        assert status["contextRisk"]["riskZone"] in {"READY", "TRUST_GATE_NEEDED", "BACKUP_NEEDED", "SAVE_FIRST"}
        assert status["contextRisk"]["rehydratePreview"]["readOnly"] is True
        assert "Total Recall Rehydrate Preview" in status["contextRisk"]["rehydratePreview"]["text"]
        assert [step["name"] for step in status["contextRisk"]["actionLadder"]] == [
            "Save Restore Point",
            "Verify",
            "Trust Gate",
            "Backup",
            "Rehydrate Preview",
            "Start fresh hydrated Hermes session",
        ]
        assert status["contextRisk"]["actionLadder"][-1]["enabled"] is False
        assert status["agentFleet"]["schema"] == "total-recall-agent-fleet-v1"
        assert status["agentFleet"]["isolation"]["default"] == "profile-local"
        assert status["agentFleet"]["isolation"]["silentSharedMemory"] is False
        assert isinstance(status["agentFleet"]["profiles"], list)
        assert status["agentFleet"]["federation"]["requiresExplicitAuthorization"] is True
        assert "hf_" not in json.dumps(status["agentFleet"])
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


def test_dashboard_agent_fleet_reads_profile_boundaries(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    profile_home = hermes_home / "profiles" / "sparky"
    profile_tr_home = profile_home / "total-recall"
    profile_tr_home.joinpath("checkpoints").mkdir(parents=True)
    profile_tr_home.joinpath("incidents").mkdir(parents=True)
    profile_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(
        "memory:\n  memory_enabled: true\n  provider: builtin\ncompression:\n  enabled: true\n  threshold: 0.5\n",
        encoding="utf-8",
    )
    (profile_home / "config.yaml").write_text(
        "memory:\n  memory_enabled: true\n  provider: total-recall\n  total-recall:\n    auto_rehydrate:\n      enabled: true\n      context_threshold: 0.55\ncompression:\n  enabled: true\n  threshold: 0.55\n",
        encoding="utf-8",
    )
    (profile_tr_home / "checkpoints" / "checkpoint_sparky.json").write_text(
        json.dumps({"event_count": 4, "created_at": "2026-01-01T00:00:00Z", "label": "sparky"}),
        encoding="utf-8",
    )
    (profile_tr_home / "incidents" / "incident_open.json").write_text(
        json.dumps({"incident_id": "inc_1", "status": "OPEN"}),
        encoding="utf-8",
    )
    (profile_tr_home / "ledger").mkdir()
    (profile_tr_home / "ledger" / "events.jsonl").write_text("{}\n{}\n{}\n{}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "active-home", enable_lancedb=False, enable_qmd=False))
    backup_dir = tmp_path / "backups"
    core.ingest(kind="note", text="Active dashboard memory.", session_id="dashboard", scope="private")
    core.checkpoint(session_id="dashboard")

    with _dashboard(core, backup_dir) as base_url:
        fleet = _get_json(base_url + "/api/agent-fleet")
        assert fleet["schema"] == "total-recall-agent-fleet-v1"
        names = {profile["profile"] for profile in fleet["profiles"]}
        assert {"default", "sparky"}.issubset(names)
        sparky = next(profile for profile in fleet["profiles"] if profile["profile"] == "sparky")
        assert sparky["gateway"] in {"running", "stopped"}
        assert sparky["memoryProvider"] == "total-recall"
        assert sparky["totalRecallHomeExists"] is True
        assert sparky["latestCheckpoint"] == "checkpoint_sparky.json"
        assert sparky["checkpointLagEvents"] == 0
        assert sparky["openIncidents"] == 1
        assert sparky["compressionThreshold"] == 0.55
        assert sparky["autoRehydrateThreshold"] == 0.55
        assert sparky["verdict"] == "Trust Gate needed"
        assert sparky["memoryIsolation"] == "profile-local"
        assert fleet["federation"]["targetCount"] == 0
        assert fleet["federation"]["status"] == "isolated"


def test_hf_wizard_status_session_repo_and_restore_safety(tmp_path, monkeypatch):
    secret = "correct horse battery staple"
    token = "FAKE_SECRET_VALUE"
    monkeypatch.setenv("PATH", str(_install_fake_hf(tmp_path, leak=token)) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("HF_TOKEN", token)
    monkeypatch.setenv("TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE", secret)
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "home", enable_lancedb=False, enable_qmd=False))
    backup_dir = tmp_path / "backups"
    core.ingest(kind="note", text="HF wizard restore safety proof.", session_id="wizard", scope="private")
    active_count = core.health()["eventCount"]

    with _dashboard(core, backup_dir) as base_url:
        with urlopen(base_url + "/", timeout=10) as response:
            html = response.read().decode("utf-8")
        assert "Hugging Face Backup Wizard" in html
        assert "Refresh status" in html
        assert "Validate private dataset" in html
        assert "Create private dataset" in html
        assert "Save passphrase for this session" in html
        assert "Clear passphrase" in html
        assert "Export encrypted clone and upload" in html
        assert "Restore into temporary test home" in html
        assert "Uploaded is not green. Restorable + verified + trust-gated is green." in html
        assert "Active memory was not replaced." in html

        status = _get_json(base_url + "/api/hf/wizard/status")
        assert status["schema"] == "total-recall-hf-wizard-v1"
        assert status["session"]["tokenValueVisible"] is False
        assert status["activeRestore"]["enabled"] is False
        assert token not in json.dumps(status)
        assert secret not in json.dumps(status)

        posted = _post_json(base_url + "/api/hf/session/passphrase", {"passphrase": secret})
        assert posted["passphrasePresent"] is True
        assert secret not in json.dumps(posted)
        status = _get_json(base_url + "/api/hf/wizard/status")
        assert status["session"]["passphrasePresent"] is True
        assert secret not in json.dumps(status)
        cleared = _post_json(base_url + "/api/hf/session/clear")
        assert cleared["passphrasePresent"] is False
        assert _get_json(base_url + "/api/hf/wizard/status")["session"]["passphrasePresent"] is False

        invalid = _post_json_allow_error(base_url + "/api/hf/repo/validate", {"repoId": "not-a-repo"})
        assert invalid["ok"] is False
        assert invalid["error"] == "invalid_repo_id"
        valid = _post_json(base_url + "/api/hf/repo/validate", {"repoId": "owner/private-dataset"})
        assert valid["ok"] is True
        assert valid["private"] is True
        assert token not in json.dumps(valid)

        _post_json(base_url + "/api/hf/session/passphrase", {"passphrase": secret})
        upload = _post_json(base_url + "/api/hf/export-upload", {"repoId": "owner/private-dataset"})
        assert upload["status"] == "UPLOADED"
        assert upload["eventCount"] >= active_count
        assert upload["upload"]["ok"] is True
        assert upload["readyForGreen"] is False
        assert secret not in json.dumps(upload)
        assert token not in json.dumps(upload)

        _post_json(base_url + "/api/hf/session/passphrase", {"passphrase": "wrong passphrase"})
        failed = _post_json_allow_error(base_url + "/api/hf/restore-test", {"repoId": "owner/private-dataset", "localDir": str(core.home / "portable-clones")})
        assert failed["ok"] is False
        assert failed["readyForGreen"] is False
        assert core.health()["eventCount"] == active_count

        _post_json(base_url + "/api/hf/session/passphrase", {"passphrase": secret})
        restored = _post_json_allow_error(base_url + "/api/hf/restore-test", {"repoId": "owner/private-dataset", "localDir": str(core.home / "portable-clones")})
        assert restored["ok"] is False
        assert restored["status"] == "LOCAL_TEST_ONLY"
        assert restored["readyForGreen"] is False
        assert restored["restoreTest"]["downloadSource"] == "local"
        assert restored["restoreTest"]["downloadOk"] is False
        assert restored["restoreTest"]["activeHomeUnchanged"] is True
        assert core.health()["eventCount"] == active_count

        bundle = next((core.home / "portable-clones").glob("total-recall-portable-clone-*.tar.gz.enc"))
        bundle_restore = _post_json_allow_error(base_url + "/api/hf/restore-test", {"repoId": "owner/private-dataset", "bundle": str(bundle)})
        assert bundle_restore["ok"] is False
        assert bundle_restore["status"] == "LOCAL_TEST_ONLY"
        assert bundle_restore["readyForGreen"] is False
        assert _get_json(base_url + "/api/hf/wizard/status")["readyForGreen"] is False

        remote_restored = _post_json(base_url + "/api/hf/restore-test", {"repoId": "owner/private-dataset"})
        assert remote_restored["restoreTest"]["activeHome"] == str(core.home)
        assert remote_restored["restoreTest"]["testHome"] != str(core.home)
        assert "total-recall-hf-restore-test." in remote_restored["restoreTest"]["testHome"]
        assert remote_restored["restoreTest"]["activeHomeUnchanged"] is True
        assert remote_restored["restoreTest"]["downloadSource"] == "huggingface"
        assert remote_restored["restoreTest"]["downloadOk"] is True
        assert remote_restored["readyForGreen"] is True
        assert remote_restored["restoreTest"]["failedRequired"] == 0
        wizard_status = _get_json(base_url + "/api/hf/wizard/status")
        assert wizard_status["readyForGreen"] is True
        assert wizard_status["lastRestoreTest"]["downloadSource"] == "huggingface"

        monkeypatch.setenv("TOTAL_RECALL_FAKE_HF_DOWNLOAD_FAIL", "1")
        failed_remote_restore = _post_json_allow_error(base_url + "/api/hf/restore-test", {"repoId": "owner/private-dataset"})
        assert failed_remote_restore["ok"] is False
        assert failed_remote_restore["error"] == "hf_download_failed"
        assert failed_remote_restore["readyForGreen"] is False
        stale_guard_status = _get_json(base_url + "/api/hf/wizard/status")
        assert stale_guard_status["readyForGreen"] is False
        assert stale_guard_status["lastRestoreTest"]["ok"] is False
        assert stale_guard_status["lastRestoreTest"]["status"] == "HF_DOWNLOAD_FAILED"
        assert stale_guard_status["lastRestoreTest"]["downloadSource"] == "huggingface"
        assert stale_guard_status["lastRestoreTest"]["downloadOk"] is False
        assert stale_guard_status["lastRestoreTest"]["activeHomeUnchanged"] is True
        assert core.health()["eventCount"] == active_count


def test_hf_wizard_public_or_unknown_visibility_not_green(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(_install_fake_hf(tmp_path, private=False)) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE", "secret")
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "home", enable_lancedb=False, enable_qmd=False))
    with _dashboard(core, tmp_path / "backups") as base_url:
        public = _post_json_allow_error(base_url + "/api/hf/repo/validate", {"repoId": "owner/public-dataset"})
        assert public["ok"] is False
        assert public["private"] is False
        assert public["green"] is False

        _post_json(base_url + "/api/hf/session/passphrase", {"passphrase": "secret"})
        restore = _post_json_allow_error(base_url + "/api/hf/restore-test", {"repoId": "owner/public-dataset"})
        assert restore["ok"] is False
        assert restore["readyForGreen"] is False
        assert restore["status"] == "REPO_NOT_PRIVATE"


def test_hf_wizard_validate_uses_hf_bundled_python_when_repo_info_missing(tmp_path, monkeypatch):
    hf_python = tmp_path / "hf-python"
    hf_python.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "print(json.dumps({'exists': True, 'private': True, 'id': sys.argv[-1]}))\n",
        encoding="utf-8",
    )
    hf_python.chmod(0o755)
    monkeypatch.setenv(
        "PATH",
        str(_install_fake_hf(tmp_path, no_repo_info=True, shebang=f"#!{hf_python}")) + os.pathsep + os.environ.get("PATH", ""),
    )
    core = TotalRecallCore(TotalRecallConfig(home=tmp_path / "home", enable_lancedb=False, enable_qmd=False))
    with _dashboard(core, tmp_path / "backups") as base_url:
        validated = _post_json(base_url + "/api/hf/repo/validate", {"repoId": "owner/private-dataset"})
        assert validated["ok"] is True
        assert validated["exists"] is True
        assert validated["private"] is True
        assert validated["status"] == "private"


def test_hf_wizard_redacts_colon_and_bearer_secrets():
    secret_terms = {
        "tok": "token",
        "access": "access_token",
        "api_dash": "api-key",
        "api_under": "api_key",
        "auth": "Authorization",
        "phrase": "passphrase",
        "pwd": "password",
        "secret": "secret",
    }
    detail = " ".join([
        f"{secret_terms['tok']}: SECRET1",
        f"{secret_terms['access']}: SECRET2",
        f"{secret_terms['api_dash']}: SECRET3",
        f"{secret_terms['api_under']}: SECRET4",
        f"{secret_terms['auth']}: Bearer SECRET5",
        f"{secret_terms['phrase']}: SECRET6",
        f"{secret_terms['pwd']}: SECRET7",
        f"{secret_terms['secret']}: SECRET8",
        "hf_abcdef123456",
    ])
    payload = {"detail": detail, "api_key": "SECRET9", "nested": {"password": "SECRET10"}}
    redacted = json.dumps(_redact_payload(payload))
    for secret in ["SECRET1", "SECRET2", "SECRET3", "SECRET4", "SECRET5", "SECRET6", "SECRET7", "SECRET8", "SECRET9", "SECRET10", "hf_abcdef123456"]:
        assert secret not in redacted
    assert "[redacted]" in redacted
    assert "BEARER_LEAK" not in _redacted_line(f"{secret_terms['auth']}: Bearer BEARER_LEAK")
    assert "PASSPHRASE_LEAK" not in _redacted_line(f"{secret_terms['phrase']}: PASSPHRASE_LEAK")
