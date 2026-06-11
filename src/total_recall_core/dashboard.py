from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from .api import TotalRecallConfig, TotalRecallCore


def run_dashboard(
    *,
    home: Path,
    host: str = "127.0.0.1",
    port: int = 8899,
    backup_dir: Path,
    keep: int = 14,
    keep_days: int | None = None,
) -> None:
    core = TotalRecallCore(TotalRecallConfig(home=home))
    server = ThreadingHTTPServer((host, port), _handler(core=core, backup_dir=backup_dir, keep=keep, keep_days=keep_days))
    print(f"Total Recall dashboard: http://{host}:{server.server_port}")
    server.serve_forever()


def _handler(*, core: TotalRecallCore, backup_dir: Path, keep: int, keep_days: int | None) -> type[BaseHTTPRequestHandler]:
    wizard_session: Dict[str, Any] = {
        "passphrase": "",
        "passphraseExpiresAt": 0.0,
        "repo": {"repoId": _hf_status(core).get("repoId"), "exists": None, "private": None, "status": "not_validated"},
        "lastExport": None,
        "lastRestoreTest": None,
    }

    class TotalRecallDashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(_CONTROL_CENTER_HTML)
                return
            if parsed.path == "/api/status":
                backup = core.backup_status(str(backup_dir))
                self._send_json({
                    "ok": True,
                    "health": core.health(),
                    "index": core.index_status(),
                    "backup": backup,
                    "backupReadiness": _backup_readiness(core, backup_dir=backup_dir, backup=backup),
                    "summary": _summary(core),
                    "trustGate": _trust_gate_summary(core),
                    "knowledge": _knowledge_summary(core),
                    "mcp": _mcp_summary(core),
                    "hf": _hf_status(core),
                    "hfWizard": _hf_wizard_status(core, wizard_session),
                    "portable": _portable_status(core),
                    "loops": _loop_inbox_summary(core),
                    "providers": _providers(),
                    "contextRisk": _context_risk(core, backup_dir=backup_dir, backup=backup),
                    "agentFleet": _agent_fleet(core),
                    "setupChecklist": _setup_checklist(core, backup_dir=backup_dir, backup=backup),
                    "policy": {
                        "backupDir": str(backup_dir.expanduser()),
                        "keep": keep,
                        "keepDays": keep_days,
                        "home": str(core.home),
                        "defaultVaultDir": str(Path.home() / "TotalRecallVault"),
                    },
                })
                return
            if parsed.path == "/api/hf/status":
                self._send_json(_hf_status(core))
                return
            if parsed.path == "/api/hf/wizard/status":
                self._send_json(_hf_wizard_status(core, wizard_session))
                return
            if parsed.path == "/api/portable/status":
                self._send_json(_portable_status(core))
                return
            if parsed.path == "/api/loops/inbox":
                self._send_json(_loop_inbox_summary(core))
                return
            if parsed.path == "/api/context-risk":
                backup = core.backup_status(str(backup_dir))
                self._send_json(_context_risk(core, backup_dir=backup_dir, backup=backup))
                return
            if parsed.path == "/api/rehydrate-preview":
                backup = core.backup_status(str(backup_dir))
                risk = _context_risk(core, backup_dir=backup_dir, backup=backup)
                self._send_json(risk.get("rehydratePreview") or {})
                return
            if parsed.path == "/api/agent-fleet":
                self._send_json(_agent_fleet(core))
                return
            if parsed.path == "/api/setup/checklist":
                backup = core.backup_status(str(backup_dir))
                self._send_json(_setup_checklist(core, backup_dir=backup_dir, backup=backup))
                return
            if parsed.path == "/api/backups/download":
                query = parse_qs(parsed.query)
                path = Path((query.get("path") or [""])[0]).expanduser()
                if not _safe_backup_path(path, backup_dir):
                    self._send_json({"ok": False, "error": "unsafe_backup_path"}, status=400)
                    return
                raw = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)
                return
            if parsed.path == "/api/launchd.plist":
                query = parse_qs(parsed.query)
                hour = int((query.get("hour") or ["3"])[0])
                minute = int((query.get("minute") or ["15"])[0])
                plist = _launchd_plist(home=core.home, backup_dir=backup_dir, keep=keep, keep_days=keep_days, hour=hour, minute=minute)
                self._send("application/xml", plist)
                return
            if parsed.path == "/api/knowledge/truth":
                self._send_json(_guarded_call(lambda: core.knowledge_compiled_truth_show(format_="md")))
                return
            if parsed.path == "/api/knowledge/graph/inspect":
                query = parse_qs(parsed.query)
                entity = (query.get("entity") or [""])[0]
                source_ref = (query.get("sourceRef") or [""])[0]
                try:
                    limit = int((query.get("limit") or ["20"])[0])
                except ValueError:
                    limit = 20
                scopes = query.get("scope") or None
                self._send_json(_guarded_call(lambda: core.knowledge_graph_inspect(entity=entity, source_ref=source_ref, limit=limit, allowed_scopes=scopes)))
                return
            if parsed.path == "/api/knowledge/graph/timeline":
                query = parse_qs(parsed.query)
                entity = (query.get("entity") or [""])[0]
                at_time = (query.get("atTime") or [""])[0]
                try:
                    limit = int((query.get("limit") or ["40"])[0])
                except ValueError:
                    limit = 40
                scopes = query.get("scope") or None
                self._send_json(_guarded_call(lambda: core.knowledge_graph_timeline(entity, at_time=at_time, limit=limit, allowed_scopes=scopes)))
                return
            if parsed.path == "/api/knowledge/freshness":
                query = parse_qs(parsed.query)
                entity = (query.get("entity") or [""])[0]
                category = (query.get("category") or [""])[0]
                at_time = (query.get("atTime") or [""])[0]
                scopes = query.get("scope") or None
                self._send_json(_guarded_call(lambda: core.knowledge_freshness_report(entity=entity, category=category, at_time=at_time, allowed_scopes=scopes)))
                return
            self._send_json({"ok": False, "error": "not_found"}, status=404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/backup/run":
                self._send_json(core.backup_run(str(backup_dir), keep=keep, keep_days=keep_days))
                return
            if parsed.path == "/api/protection/fix-all":
                self._send_json(_fix_all(core, backup_dir=backup_dir, keep=keep, keep_days=keep_days))
                return
            if parsed.path == "/api/vault/export":
                payload = self._read_body_json()
                try:
                    max_events = int(payload.get("maxEvents") or 500)
                except (TypeError, ValueError):
                    max_events = 500
                try:
                    max_entities = int(payload.get("maxEntities") or 100)
                except (TypeError, ValueError):
                    max_entities = 100
                out = str(payload.get("path") or payload.get("out") or (Path.home() / "TotalRecallVault"))
                scopes = payload.get("scopes") or None
                self._send_json(_guarded_call(lambda: core.export_obsidian_vault(
                    out,
                    force=bool(payload.get("force")),
                    allowed_scopes=scopes,
                    max_events=max_events,
                    max_entities=max_entities,
                )))
                return
            if parsed.path == "/api/vault/import-preview":
                payload = self._read_body_json()
                notes = payload.get("notes")
                if isinstance(notes, str):
                    notes = [item.strip() for item in notes.split(",") if item.strip()]
                self._send_json(_guarded_call(lambda: core.vault_import_preview(
                    payload.get("vault") or payload.get("path") or "",
                    notes=notes or None,
                    session_id=str(payload.get("sessionId") or "dashboard-obsidian-import"),
                    scope=str(payload.get("scope") or "private"),
                )))
                return
            if parsed.path == "/api/vault/import-promote":
                payload = self._read_body_json()
                proposal_ids = payload.get("proposalIds")
                if isinstance(proposal_ids, str):
                    proposal_ids = [item.strip() for item in proposal_ids.split(",") if item.strip()]
                self._send_json(_guarded_call(lambda: core.vault_import_promote(
                    str(payload.get("previewId") or ""),
                    proposal_ids=proposal_ids or None,
                    session_id=str(payload.get("sessionId") or "dashboard-obsidian-import"),
                    scope=str(payload.get("scope") or ""),
                )))
                return
            if parsed.path == "/api/sources/ingest":
                payload = self._read_body_json()
                participants = payload.get("participants") or []
                if isinstance(participants, str):
                    participants = [item.strip() for item in participants.split(",") if item.strip()]
                self._send_json(_guarded_call(lambda: core.ingest_source(
                    source_type=str(payload.get("sourceType") or payload.get("type") or ""),
                    text=str(payload.get("text") or ""),
                    title=str(payload.get("title") or ""),
                    actor=str(payload.get("actor") or ""),
                    occurred_at=str(payload.get("occurredAt") or ""),
                    participants=participants,
                    session_id=str(payload.get("sessionId") or "dashboard-source"),
                    scope=str(payload.get("scope") or "private"),
                    dry_run=bool(payload.get("dryRun")),
                )))
                return
            if parsed.path == "/api/remote/sync":
                payload = self._read_body_json()
                self._send_json(_remote_sync(core, backup_dir=backup_dir, selected=payload.get("providers") or []))
                return
            if parsed.path == "/api/remote/upload":
                payload = self._read_body_json()
                selected = payload.get("providers") or []
                backup = core.backup_run(str(backup_dir), keep=keep, keep_days=keep_days)
                self._send_json(_remote_upload_result(core, backup_dir=backup_dir, selected=selected, backup=backup))
                return
            if parsed.path == "/api/hf/session/passphrase":
                payload = self._read_body_json()
                passphrase = str(payload.get("passphrase") or "")
                ttl_seconds = _safe_int(payload.get("ttlSeconds"), 3600)
                wizard_session["passphrase"] = passphrase
                wizard_session["passphraseExpiresAt"] = time.time() + max(60, min(ttl_seconds, 24 * 3600)) if passphrase else 0.0
                self._send_json({"ok": True, "schema": "total-recall-hf-wizard-v1", "passphrasePresent": _wizard_passphrase_present(wizard_session), "tokenValueVisible": False})
                return
            if parsed.path == "/api/hf/session/clear":
                wizard_session["passphrase"] = ""
                wizard_session["passphraseExpiresAt"] = 0.0
                self._send_json({"ok": True, "schema": "total-recall-hf-wizard-v1", "passphrasePresent": False, "tokenValueVisible": False})
                return
            if parsed.path == "/api/hf/repo/validate":
                payload = self._read_body_json()
                result = _hf_repo_validate(str(payload.get("repoId") or ""))
                wizard_session["repo"] = {key: result.get(key) for key in ["repoId", "exists", "private", "status"]}
                self._send_json(result, status=200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/hf/repo/create":
                payload = self._read_body_json()
                result = _hf_repo_create(str(payload.get("repoId") or ""))
                wizard_session["repo"] = {key: result.get(key) for key in ["repoId", "exists", "private", "status"]}
                self._send_json(result, status=200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/hf/export-upload":
                payload = self._read_body_json()
                result = _hf_export_upload(core, wizard_session, repo_id=str(payload.get("repoId") or ""))
                wizard_session["lastExport"] = result.get("export") if result.get("export") else wizard_session.get("lastExport")
                self._send_json(result, status=200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/hf/restore-test":
                payload = self._read_body_json()
                result = _hf_restore_test(core, wizard_session, repo_id=str(payload.get("repoId") or ""), local_dir=str(payload.get("localDir") or ""), bundle=str(payload.get("bundle") or ""))
                wizard_session["lastRestoreTest"] = result.get("restoreTest") if result.get("restoreTest") else _hf_restore_failure_summary(result)
                self._send_json(result, status=200 if result.get("ok") else 400)
                return
            if parsed.path == "/api/checkpoint":
                self._send_json(core.checkpoint(session_id="dashboard", label="manual_dashboard_checkpoint"))
                return
            if parsed.path == "/api/doctor":
                self._send_json(core.doctor())
                return
            if parsed.path == "/api/verify":
                self._send_json(core.verify())
                return
            if parsed.path == "/api/trust/verify":
                self._send_json(_guarded_call(core.trust_gate_run))
                return
            if parsed.path == "/api/knowledge/query":
                payload = self._read_body_json()
                self._send_json(_guarded_call(lambda: core.knowledge_query(
                    str(payload.get("query") or ""),
                    mode=str(payload.get("mode") or "normal"),
                    session_id=str(payload.get("sessionId") or ""),
                    max_results=int(payload.get("maxResults") or 8),
                    at_time=str(payload.get("atTime") or ""),
                    allowed_scopes=payload.get("scopes") or None,
                    federate=payload.get("federate") or None,
                    federation_authorized=bool(payload.get("federationAuthorized")),
                    external_providers=payload.get("externalProviders") or None,
                    external_provider_authorized=bool(payload.get("externalProviderAuthorized")),
                )))
                return
            if parsed.path == "/api/knowledge/index/rebuild":
                self._send_json(_guarded_call(core.knowledge_index_rebuild))
                return
            if parsed.path == "/api/knowledge/graph/rebuild":
                self._send_json(_guarded_call(core.knowledge_graph_rebuild))
                return
            if parsed.path == "/api/knowledge/truth/build":
                self._send_json(_guarded_call(core.knowledge_compiled_truth_build))
                return
            if parsed.path == "/api/knowledge/synthesize/run":
                self._send_json(_guarded_call(core.knowledge_synthesize_run))
                return
            if parsed.path == "/api/knowledge/evaluate/run":
                self._send_json(_guarded_call(core.knowledge_evaluate_run))
                return
            self._send_json({"ok": False, "error": "not_found"}, status=404)

        def _send_json(self, payload: Dict[str, Any], *, status: int = 200) -> None:
            self._send("application/json", json.dumps(payload, indent=2, ensure_ascii=False), status=status)

        def _read_body_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                return {}

        def _send_html(self, html: str) -> None:
            self._send("text/html; charset=utf-8", html)

        def _send(self, content_type: str, body: str, *, status: int = 200) -> None:
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

    return TotalRecallDashboardHandler


def _summary(core: TotalRecallCore) -> Dict[str, Any]:
    health = core.health()
    latest = health.get("latestCheckpoint")
    checkpoint = {}
    if latest:
        try:
            checkpoint = core._read_json(Path(str(latest)))  # dashboard is part of the local package.
        except Exception:
            checkpoint = {}
    event_count = int(health.get("eventCount") or 0)
    checkpoint_event_count = int(checkpoint.get("event_count") or 0) if checkpoint else 0
    lag = event_count - checkpoint_event_count if checkpoint else event_count
    if not checkpoint:
        checkpoint_status = "missing"
    elif lag == 0:
        checkpoint_status = "current"
    else:
        checkpoint_status = "stale"
    return {
        "eventCount": event_count,
        "openIncidents": int(health.get("openIncidents") or 0),
        "latestCheckpoint": latest,
        "checkpointEventCount": checkpoint_event_count,
        "checkpointLagEvents": lag,
        "checkpointStatus": checkpoint_status,
    }


def _backup_readiness(core: TotalRecallCore, *, backup_dir: Path, backup: Dict[str, Any] | None = None) -> Dict[str, Any]:
    backup = backup or core.backup_status(str(backup_dir))
    summary = _summary(core)
    health = core.health()
    sync = _guarded_call(lambda: core.sync_status(str(backup_dir)))
    latest_path = backup.get("latest")
    latest = next((item for item in backup.get("backups", []) if item.get("path") == latest_path), None)
    latest_modified = (latest or {}).get("modified")
    latest_age_seconds = _age_seconds(latest_modified) if latest_modified else None
    relation = sync.get("relation") if sync.get("ok") else "unknown"
    local_event_count = int(((sync.get("local") or {}).get("eventCount") or summary.get("eventCount") or 0))
    archive_checkpoint = ((sync.get("archive") or {}).get("latestCheckpoint") or {}) if sync.get("ok") else {}
    archive_event_count = int(archive_checkpoint.get("event_count") or 0) if archive_checkpoint else None
    events_not_backed_up = max(0, local_event_count - archive_event_count) if archive_event_count is not None else local_event_count
    checkpoint_current = summary.get("checkpointStatus") == "current"
    no_open_incidents = int(summary.get("openIncidents") or 0) == 0
    verified_ledger = bool(health.get("ok"))
    rehydrate_ready = verified_ledger and checkpoint_current and no_open_incidents

    if not backup.get("count"):
        status = "NO_BACKUP"
        tone = "warn"
        next_action = "Click Fix All to save a restore point, verify memory, and write the first backup archive."
    elif relation == "in_sync" and rehydrate_ready:
        status = "CURRENT"
        tone = "ok"
        next_action = "Memory is protected. Click Fix All again after meaningful new work."
    elif relation == "local_ahead":
        status = "BACKUP_BEHIND"
        tone = "warn"
        next_action = "Click Fix All so the latest backup covers the current memory vault."
    elif not checkpoint_current:
        status = "CHECKPOINT_STALE"
        tone = "warn"
        next_action = "Save a restore point before continuing, then back it up."
    elif not no_open_incidents:
        status = "INCIDENTS_OPEN"
        tone = "bad"
        next_action = "Resolve safety incidents before trusting restored memory."
    elif relation == "archive_ahead":
        status = "ARCHIVE_AHEAD"
        tone = "warn"
        next_action = "Latest archive is ahead; inspect/import before continuing local work."
    elif relation == "diverged":
        status = "DIVERGED"
        tone = "bad"
        next_action = f"Do not auto-merge. Use sync fork-import {latest_path or '<bundle>'} to quarantine the archive-only suffix."
    else:
        status = "CHECK"
        tone = "warn"
        next_action = sync.get("message") or "Click Fix All, then refresh this panel."

    return {
        "ok": status == "CURRENT",
        "status": status,
        "tone": tone,
        "backupDir": str(backup_dir.expanduser()),
        "latestPath": latest_path,
        "latestName": Path(str(latest_path)).name if latest_path else None,
        "latestModified": latest_modified,
        "latestAgeSeconds": latest_age_seconds,
        "latestAgeLabel": _age_label(latest_age_seconds),
        "backupCount": int(backup.get("count") or 0),
        "totalBytes": int(backup.get("totalBytes") or 0),
        "relation": relation,
        "message": sync.get("message") or "Backup relation unavailable.",
        "localEventCount": local_event_count,
        "archiveEventCount": archive_event_count,
        "eventsNotBackedUp": events_not_backed_up,
        "checkpointStatus": summary.get("checkpointStatus"),
        "checkpointLagEvents": int(summary.get("checkpointLagEvents") or 0),
        "openIncidents": int(summary.get("openIncidents") or 0),
        "rehydrateReady": rehydrate_ready,
        "compactionRule": "Before a long session compresses: save and verify a restore point. After restart: restore only from verified Total Recall. Backups protect recovery; they are not memory authority by themselves.",
        "nextAction": next_action,
        "forkImportAction": f"total-recall sync fork-import {latest_path}" if relation == "diverged" and latest_path else "",
        "sync": sync,
    }


def _age_seconds(iso_timestamp: str) -> int | None:
    try:
        modified = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except Exception:
        return None
    return max(0, int((datetime.now(timezone.utc) - modified).total_seconds()))


def _age_label(seconds: int | None) -> str:
    if seconds is None:
        return "unknown age"
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m old"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h old"
    days = hours // 24
    return f"{days}d old"


def _guarded_call(fn: Any) -> Dict[str, Any]:
    try:
        payload = fn()
        if isinstance(payload, dict):
            return payload
        return {"ok": True, "result": payload}
    except Exception as exc:
        return {"ok": False, "status": "ERROR", "error": str(exc)}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _wizard_passphrase_present(session: Dict[str, Any]) -> bool:
    if not session.get("passphrase"):
        return False
    if float(session.get("passphraseExpiresAt") or 0) < time.time():
        session["passphrase"] = ""
        session["passphraseExpiresAt"] = 0.0
        return False
    return True


def _wizard_passphrase(session: Dict[str, Any]) -> str:
    return str(session.get("passphrase") or "") if _wizard_passphrase_present(session) else ""


def _hf_wizard_status(core: TotalRecallCore, session: Dict[str, Any]) -> Dict[str, Any]:
    repo = dict(session.get("repo") or {})
    if not repo.get("repoId"):
        repo["repoId"] = _hf_status(core).get("repoId")
    last_restore = session.get("lastRestoreTest")
    ready = _hf_restore_ready_for_green(repo, last_restore)
    return {
        "ok": True,
        "schema": "total-recall-hf-wizard-v1",
        "home": str(core.home),
        "hf": _hf_status(core),
        "portable": _portable_status(core),
        "session": {"passphrasePresent": _wizard_passphrase_present(session), "tokenValueVisible": False},
        "repo": {
            "repoId": repo.get("repoId") or None,
            "exists": repo.get("exists"),
            "private": repo.get("private"),
            "status": repo.get("status") or "not_validated",
        },
        "lastExport": _redact_payload(session.get("lastExport")),
        "lastRestoreTest": _redact_payload(last_restore),
        "readyForGreen": ready,
        "activeRestore": {"enabled": False, "reason": "v1 only restores into a fresh temporary test home; active memory replacement is not exposed."},
    }


def _hf_restore_ready_for_green(repo: Dict[str, Any], last_restore: Any) -> bool:
    if not isinstance(last_restore, dict):
        return False
    return all([
        repo.get("private") is True,
        last_restore.get("downloadSource") == "huggingface",
        last_restore.get("downloadOk") is True,
        last_restore.get("verifyOk") is True,
        last_restore.get("trustOk") is True,
        int(last_restore.get("failedRequired") or 0) == 0,
        last_restore.get("activeHomeUnchanged") is True,
        last_restore.get("ledgerMatch") is True,
    ])


def _hf_restore_failure_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    error = str(result.get("error") or "").strip().lower()
    status = "HF_DOWNLOAD_FAILED" if error == "hf_download_failed" else str(result.get("status") or "").strip().upper()
    if not status:
        status = "RESTORE_TEST_FAILED"
    source = "local" if status == "LOCAL_TEST_ONLY" else "huggingface"
    return {
        "ok": False,
        "status": status,
        "downloadSource": source,
        "downloadOk": False,
        "verifyOk": False,
        "trustOk": False,
        "failedRequired": None,
        "activeHomeUnchanged": bool(result.get("activeHomeUnchanged")),
        "ledgerMatch": False,
        "readyForGreen": False,
    }


def _valid_hf_repo_id(repo_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", repo_id or ""))


def _hf_cli_bin() -> str | None:
    return shutil.which("hf") or shutil.which("huggingface-cli")


def _hf_repo_validate(repo_id: str) -> Dict[str, Any]:
    repo_id = (repo_id or "").strip()
    if not _valid_hf_repo_id(repo_id):
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "invalid_repo_id", "repoId": repo_id, "exists": False, "private": None, "status": "invalid"}
    hf_bin = _hf_cli_bin()
    if not hf_bin:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "hf_cli_not_found", "repoId": repo_id, "exists": None, "private": None, "status": "unknown"}
    commands = [
        [hf_bin, "repo", "info", repo_id, "--repo-type", "dataset", "--json"],
        [hf_bin, "repo", "info", repo_id, "--type", "dataset", "--json"],
        [hf_bin, "repo", "info", repo_id, "--repo-type", "dataset"],
    ]
    last = None
    for command in commands:
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        except Exception as exc:
            last = {"returncode": 1, "detail": exc.__class__.__name__}
            continue
        out = _redacted_line((run.stdout or "") + " " + (run.stderr or ""))
        last = {"returncode": run.returncode, "detail": out}
        if run.returncode != 0:
            continue
        private = _parse_hf_private(run.stdout, run.stderr)
        status = "private" if private is True else "public" if private is False else "visibility_unknown"
        return {"ok": private is True, "schema": "total-recall-hf-wizard-v1", "repoId": repo_id, "exists": True, "private": private, "status": status, "green": private is True, "detail": out}
    api_info = _hf_repo_info_via_hf_python(hf_bin, repo_id)
    if api_info.get("exists"):
        private = api_info.get("private")
        status = "private" if private is True else "public" if private is False else "visibility_unknown"
        return {"ok": private is True, "schema": "total-recall-hf-wizard-v1", "repoId": repo_id, "exists": True, "private": private, "status": status, "green": private is True, "detail": api_info.get("detail")}
    return {"ok": False, "schema": "total-recall-hf-wizard-v1", "repoId": repo_id, "exists": False, "private": None, "status": "not_found_or_unknown", "green": False, "detail": api_info.get("detail") or (last or {}).get("detail")}


def _hf_python_from_cli(hf_bin: str) -> str | None:
    try:
        first = Path(hf_bin).read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except Exception:
        return None
    if not first.startswith("#!"):
        return None
    command = first[2:].strip()
    if not command or "python" not in command:
        return None
    parts = command.split()
    if not parts:
        return None
    if Path(parts[0]).name == "env" and len(parts) > 1:
        return shutil.which(parts[1])
    return parts[0] if Path(parts[0]).exists() else shutil.which(Path(parts[0]).name)


def _hf_repo_info_via_hf_python(hf_bin: str, repo_id: str) -> Dict[str, Any]:
    """Query repo metadata using the Python bundled with `hf` when CLI lacks `repo info`."""
    py_bin = _hf_python_from_cli(hf_bin)
    if not py_bin:
        return {"exists": False, "detail": "hf_python_unavailable"}
    script = """
import json, sys
from huggingface_hub import HfApi
try:
    info = HfApi().repo_info(sys.argv[1], repo_type='dataset')
    print(json.dumps({'exists': True, 'private': getattr(info, 'private', None), 'id': getattr(info, 'id', sys.argv[1])}))
except Exception as exc:
    print(json.dumps({'exists': False, 'error': exc.__class__.__name__, 'detail': str(exc)[:240]}))
"""
    try:
        run = subprocess.run([py_bin, "-c", script, repo_id], capture_output=True, text=True, timeout=20, check=False)
    except Exception as exc:
        return {"exists": False, "detail": exc.__class__.__name__}
    try:
        payload = json.loads(run.stdout or "{}")
    except Exception:
        payload = {"exists": False, "detail": _redacted_line((run.stderr or run.stdout)[-500:])}
    if run.returncode != 0 and not payload.get("exists"):
        payload.setdefault("detail", _redacted_line((run.stderr or run.stdout)[-500:]))
    payload["detail"] = _redacted_line(str(payload.get("detail") or "hf api metadata lookup"))
    return payload


def _parse_hf_private(stdout: str, stderr: str = "") -> bool | None:
    text = (stdout or "") + "\n" + (stderr or "")
    try:
        payload = json.loads(stdout or "{}")
        if isinstance(payload.get("private"), bool):
            return bool(payload.get("private"))
    except Exception:
        pass
    lowered = text.lower()
    if re.search(r"\bprivate\b\s*[:=]\s*(true|yes)", lowered) or "visibility: private" in lowered:
        return True
    if re.search(r"\bprivate\b\s*[:=]\s*(false|no)", lowered) or "visibility: public" in lowered or "public" in lowered:
        return False
    return None


def _hf_repo_create(repo_id: str) -> Dict[str, Any]:
    repo_id = (repo_id or "").strip()
    if not _valid_hf_repo_id(repo_id):
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "invalid_repo_id", "repoId": repo_id, "exists": False, "private": None, "status": "invalid"}
    hf_bin = _hf_cli_bin()
    if not hf_bin:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "hf_cli_not_found", "repoId": repo_id, "exists": None, "private": None, "status": "unknown"}
    command = [hf_bin, "repo", "create", repo_id, "--type", "dataset", "--private", "--exist-ok"]
    run = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if run.returncode != 0:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "repoId": repo_id, "exists": None, "private": None, "status": "create_failed", "detail": _redacted_line(run.stderr or run.stdout)}
    validated = _hf_repo_validate(repo_id)
    if validated.get("private") is not True:
        validated["ok"] = False
        validated["status"] = validated.get("status") or "visibility_unknown"
    return validated


def _hf_export_upload(core: TotalRecallCore, session: Dict[str, Any], *, repo_id: str) -> Dict[str, Any]:
    repo_id = (repo_id or ((session.get("repo") or {}).get("repoId") or "")).strip()
    secret = _wizard_passphrase(session)
    if not secret:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "passphrase_required"}
    if not _valid_hf_repo_id(repo_id):
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "invalid_repo_id", "repoId": repo_id}
    repo = _hf_repo_validate(repo_id)
    session["repo"] = {key: repo.get(key) for key in ["repoId", "exists", "private", "status"]}
    if repo.get("private") is False:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "repo_not_private", "repo": repo}
    result = core.portable_clone_export(out_dir=core.home / "portable-clones", provider="huggingface", upload=True, repo_id=repo_id, passphrase=secret)
    redacted = _redact_export_summary(result)
    return {
        "ok": bool(result.get("ok")),
        "schema": "total-recall-hf-wizard-v1",
        "status": result.get("status") or ("UPLOADED" if result.get("ok") else "FAILED"),
        "eventCount": ((result.get("ledger") or {}).get("eventCount")),
        "cloneId": result.get("cloneId"),
        "bundle": Path(str(result.get("encryptedBundle") or "")).name or None,
        "manifest": Path(str(result.get("manifestFile") or "")).name or None,
        "upload": {"ok": bool((result.get("upload") or {}).get("ok"))},
        "repo": repo,
        "export": redacted,
        "readyForGreen": False,
    }


def _hf_restore_test(core: TotalRecallCore, session: Dict[str, Any], *, repo_id: str, local_dir: str = "", bundle: str = "") -> Dict[str, Any]:
    repo_id = (repo_id or ((session.get("repo") or {}).get("repoId") or "")).strip()
    secret = _wizard_passphrase(session)
    if not secret:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "passphrase_required"}
    if not _valid_hf_repo_id(repo_id):
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "invalid_repo_id", "repoId": repo_id}
    repo = _hf_repo_validate(repo_id)
    session["repo"] = {key: repo.get(key) for key in ["repoId", "exists", "private", "status"]}
    if repo.get("private") is not True:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "status": "REPO_NOT_PRIVATE", "error": "repo_not_private", "repo": repo, "readyForGreen": False, "activeRestore": {"enabled": False, "reason": "Active memory was not replaced."}}
    if local_dir or bundle:
        summary = {
            "ok": False,
            "status": "LOCAL_TEST_ONLY",
            "downloadSource": "local",
            "downloadOk": False,
            "activeHomeUnchanged": True,
            "verifyOk": False,
            "trustOk": False,
            "failedRequired": 0,
            "ledgerMatch": False,
        }
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "status": "LOCAL_TEST_ONLY", "restoreTest": summary, "readyForGreen": False, "activeRestore": {"enabled": False, "reason": "Active memory was not replaced."}}
    before = _summary(core)
    try:
        with tempfile.TemporaryDirectory(prefix="total-recall-hf-download.") as staging:
            staging_path = Path(staging)
            dl = _hf_download_latest(repo_id, staging_path)
            if not dl.get("ok"):
                dl["readyForGreen"] = False
                dl["activeHomeUnchanged"] = True
                return dl
            bundle_path = _latest_clone_bundle(staging_path)
            test_home = Path(tempfile.mkdtemp(prefix="total-recall-hf-restore-test."))
            test_core = TotalRecallCore(TotalRecallConfig(home=test_home, enable_lancedb=False, enable_qmd=False))
            restored = test_core.portable_clone_restore(str(bundle_path), passphrase=secret, replace=True)
            verified = test_core.verify()
            trust = test_core.trust_gate_run(persist=True)
            failed_required = int(((trust.get("summary") or {}).get("failedRequired") or 0))
            source_ledger = ((session.get("lastExport") or {}).get("ledger") or {})
            manifest_ledger = ((restored.get("manifest") or {}).get("ledger") or {})
            restored_state = test_core.reduce_state(write=False)
            ledger_match = True
            expected_count = source_ledger.get("eventCount") if source_ledger.get("eventCount") is not None else manifest_ledger.get("eventCount")
            restored_events = test_core._read_events(verify_chain=True)
            last_event = restored_events[-1] if restored_events else {}
            last_metadata = last_event.get("metadata") or {}
            if expected_count is not None:
                ledger_match = int(restored_state.get("event_count") or 0) == int(expected_count or 0) + 1
            if manifest_ledger.get("lastEventHash"):
                ledger_match = ledger_match and last_event.get("kind") == "re_anchor" and last_metadata.get("restored_last_event_hash") == manifest_ledger.get("lastEventHash")
            active_unchanged = int(before.get("eventCount") or 0) == int(_summary(core).get("eventCount") or 0)
            ok = bool(restored.get("ok")) and bool(verified.get("ok")) and bool(trust.get("ok")) and failed_required == 0 and ledger_match and active_unchanged
            summary = {
                "ok": ok,
                "status": "GREEN" if ok else "FAIL_CLOSED",
                "cloneId": (restored.get("manifest") or {}).get("cloneId"),
                "bundle": Path(str(bundle_path)).name,
                "downloadSource": "huggingface",
                "downloadOk": True,
                "testHome": str(test_home),
                "activeHome": str(core.home),
                "activeHomeUnchanged": active_unchanged,
                "verifyOk": bool(verified.get("ok")),
                "trustOk": bool(trust.get("ok")),
                "failedRequired": failed_required,
                "ledgerMatch": ledger_match,
                "eventCount": int(restored_state.get("event_count") or 0),
            }
            ready = _hf_restore_ready_for_green(session.get("repo") or {}, summary)
            return {"ok": ok, "schema": "total-recall-hf-wizard-v1", "status": summary["status"], "restoreTest": summary, "readyForGreen": ready, "activeRestore": {"enabled": False, "reason": "Active memory was not replaced."}}
    except Exception as exc:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "status": "FAIL_CLOSED", "error": _redacted_line(str(exc)), "readyForGreen": False, "activeRestore": {"enabled": False, "reason": "Active memory was not replaced."}}


def _hf_download_latest(repo_id: str, staging_path: Path) -> Dict[str, Any]:
    hf_bin = _hf_cli_bin()
    if not hf_bin:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "hf_cli_not_found"}
    command = [hf_bin, "download", repo_id, "--repo-type", "dataset", "--include", "total-recall-portable-clone-*.tar.gz.enc*", "--local-dir", str(staging_path)]
    run = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if run.returncode != 0:
        return {"ok": False, "schema": "total-recall-hf-wizard-v1", "error": "hf_download_failed", "detail": _redacted_line(run.stderr or run.stdout)}
    return {"ok": True, "schema": "total-recall-hf-wizard-v1", "status": "DOWNLOADED"}


def _latest_clone_bundle(path: Path) -> Path:
    candidates = sorted(path.glob("**/total-recall-portable-clone-*.tar.gz.enc"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError("encrypted_clone_not_found")
    return candidates[0]


def _redact_export_summary(result: Dict[str, Any]) -> Dict[str, Any] | None:
    if not result:
        return None
    return _redact_payload({
        "ok": result.get("ok"),
        "status": result.get("status"),
        "cloneId": result.get("cloneId"),
        "bundle": Path(str(result.get("encryptedBundle") or "")).name or None,
        "manifest": Path(str(result.get("manifestFile") or "")).name or None,
        "ledger": result.get("ledger") or {},
        "upload": {"ok": bool((result.get("upload") or {}).get("ok"))},
    })


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if re.search(r"(passphrase|password|secret|token|authorization|api[-_]?key)", str(key), re.IGNORECASE):
                out[key] = "[redacted]"
            else:
                out[key] = _redact_payload(item)
        return out
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return _redacted_line(value)
    return value


def _fix_all(core: TotalRecallCore, *, backup_dir: Path, keep: int, keep_days: int | None) -> Dict[str, Any]:
    steps: list[Dict[str, Any]] = []

    def step(name: str, fn: Any) -> Dict[str, Any]:
        result = _guarded_call(fn)
        steps.append({"name": name, "ok": result.get("ok") is not False, "result": result})
        return result

    step("save_restore_point", lambda: core.checkpoint(session_id="dashboard", label="fix_all_before_rebuild"))
    step("rebuild_search_catalog", core.knowledge_index_rebuild)
    step("rebuild_graph", core.knowledge_graph_rebuild)
    step("build_compiled_truth", core.knowledge_compiled_truth_build)
    step("run_release_gate", core.knowledge_evaluate_run)
    step("write_latest_backup", lambda: core.backup_run(str(backup_dir), keep=keep, keep_days=keep_days))
    backup = core.backup_status(str(backup_dir))
    return {
        "ok": all(item["ok"] for item in steps),
        "status": "PASS" if all(item["ok"] for item in steps) else "ATTENTION",
        "schema": "total-recall-fix-all-v1",
        "steps": steps,
        "backupReadiness": _backup_readiness(core, backup_dir=backup_dir, backup=backup),
        "summary": _summary(core),
        "knowledge": _knowledge_summary(core),
    }


def _knowledge_summary(core: TotalRecallCore) -> Dict[str, Any]:
    status = _guarded_call(core.knowledge_status)
    scorecard = _guarded_call(core.knowledge_evaluate_scorecard)
    if status.get("ok") is False:
        return {
            "ok": False,
            "status": status.get("status") or "ERROR",
            "error": status.get("error"),
            "scorecard": scorecard,
        }
    index = status.get("index") or {}
    graph = status.get("graph") or {}
    truth = status.get("compiledTruth") or {}
    synthesis = status.get("synthesis") or {}
    checks = [
        bool(status.get("ok")),
        bool(index.get("fresh")),
        graph.get("uncitedActiveItems", 0) == 0,
        truth.get("status") in {"PASS", "NO_PROJECTION"},
        synthesis.get("status") in {"PASS", "NO_SYNTHESIS"},
    ]
    return {
        **status,
        "ok": all(checks),
        "status": "PASS" if all(checks) else "DEGRADED",
        "scorecard": scorecard,
    }


def _trust_gate_summary(core: TotalRecallCore) -> Dict[str, Any]:
    status = _guarded_call(core.trust_gate_status)
    if status.get("status") == "NO_TRUST_GATE":
        return {"ok": False, "status": "NO_TRUST_GATE", "summary": {"totalChecks": 0, "passed": 0, "failedRequired": 0}}
    return status


def _mcp_summary(core: TotalRecallCore) -> Dict[str, Any]:
    return {
        "ok": True,
        "surface": "local-admin-http",
        "remoteMcp": "planned",
        "auth": "local-only-no-oauth",
        "events": "polling-json",
        "hermesProvider": "implemented",
        "home": str(core.home),
        "controls": [
            {"name": "Hermes MemoryProvider", "status": "implemented", "detail": "Lifecycle hooks, safe restore, verify, and Search Catalog tools."},
            {"name": "Remote admin HTTP", "status": "planned", "detail": "This dashboard is the local control shape; secured remote serving still needs OAuth and scope enforcement."},
            {"name": "OAuth 2.1 clients", "status": "planned", "detail": "No remote clients are accepted by this local dashboard."},
            {"name": "Live activity stream", "status": "planned", "detail": "Current control center uses explicit JSON actions and refresh."},
            {"name": "Provider adapters", "status": "guarded", "detail": "External providers require explicit authorization and redacted payloads."},
        ],
    }


def _hf_status(core: TotalRecallCore) -> Dict[str, Any]:
    """Return Hugging Face transport readiness without exposing secrets."""
    hf_bin = shutil.which("hf")
    repo_id = (
        os.getenv("TOTAL_RECALL_HF_REPO_ID")
        or os.getenv("TOTAL_RECALL_PORTABLE_CLONE_REPO_ID")
        or os.getenv("HF_REPO_ID")
        or ""
    )
    token_env_names = ["HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "HUGGING_FACE_HUB_TOKEN"]
    token_env_present = any(bool(os.getenv(name)) for name in token_env_names)
    passphrase_present = bool(os.getenv("TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE"))
    logged_in = False
    username = ""
    auth_detail = "HF CLI not found. Install/login before using Hugging Face transport."
    if hf_bin:
        try:
            result = subprocess.run(
                [hf_bin, "auth", "whoami"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode == 0:
                logged_in = True
                lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
                username = _safe_hf_username(lines[0]) if lines else "logged-in"
                auth_detail = "HF CLI is logged in. Token value is intentionally hidden."
            else:
                auth_detail = _redacted_line(result.stderr or result.stdout or "HF CLI is installed but not logged in.")
        except Exception as exc:
            auth_detail = f"HF CLI status check failed: {exc.__class__.__name__}"
    if token_env_present:
        token_source = "env-present"
    elif logged_in:
        token_source = "cli-cache"
    else:
        token_source = "missing"
    if not hf_bin:
        status = "HF_CLI_MISSING"
    elif logged_in and repo_id and passphrase_present:
        status = "READY"
    elif logged_in:
        status = "NEEDS_REPO_OR_PASSPHRASE"
    else:
        status = "LOGIN_REQUIRED"
    return {
        "ok": bool(hf_bin and logged_in),
        "schema": "total-recall-hf-status-v1",
        "status": status,
        "hfCliFound": bool(hf_bin),
        "hfCliPath": hf_bin or None,
        "loggedIn": logged_in,
        "username": username or None,
        "repoId": repo_id or None,
        "repoType": "dataset",
        "repoVisibility": "unknown" if repo_id else None,
        "tokenSource": token_source,
        "tokenValueVisible": False,
        "passphrasePresent": passphrase_present,
        "detail": auth_detail,
        "home": str(core.home),
        "instructions": _hf_instructions(repo_id or "USER/total-recall-portable-clones"),
    }


def _portable_status(core: TotalRecallCore) -> Dict[str, Any]:
    clone_dirs = [
        Path(os.getenv("TOTAL_RECALL_PORTABLE_CLONE_DIR") or "~/total-recall-portable-clones").expanduser(),
        core.home / "portable-clones",
    ]
    manifests: list[Dict[str, Any]] = []
    for clone_dir in clone_dirs:
        if not clone_dir.exists():
            continue
        for manifest_path in clone_dir.glob("total-recall-portable-clone-*.manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            manifests.append({
                "path": str(manifest_path),
                "createdAt": manifest.get("createdAt"),
                "cloneId": manifest.get("cloneId"),
                "provider": manifest.get("provider") or {},
                "ledger": manifest.get("ledger") or {},
                "encrypted": manifest.get("encrypted") or {},
            })
    manifests.sort(key=lambda item: item.get("createdAt") or item.get("path") or "", reverse=True)
    latest = manifests[0] if manifests else None
    repo_id = ((latest or {}).get("provider") or {}).get("repoId") or _hf_status(core).get("repoId") or "USER/total-recall-portable-clones"
    return {
        "ok": True,
        "schema": "total-recall-portable-status-v1",
        "status": "CLONE_AVAILABLE" if latest else "NO_CLONE_FOUND",
        "cloneDirs": [str(path) for path in clone_dirs],
        "cloneCount": len(manifests),
        "latestClone": latest,
        "restoreDefaults": {
            "testHome": "~/total-recall-restored-test",
            "replaceActiveAllowedAfter": ["restore-test", "verify", "trust-gate"],
            "destructiveConfirmation": "REPLACE ACTIVE MEMORY",
        },
        "restoreCommand": _restore_command(str(repo_id)),
    }


def _loop_inbox_summary(core: TotalRecallCore) -> Dict[str, Any]:
    payload = _guarded_call(lambda: core.loop_inbox(include_completed=False))
    loops = payload.get("loops") or []
    return {
        "ok": payload.get("ok") is not False,
        "schema": "total-recall-loop-inbox-dashboard-v1",
        "count": len(loops),
        "loops": loops,
        "mode": "read-only-review",
        "detail": "Shows loop evidence and phase only. It does not run agents or merge work.",
    }


def _agent_fleet(core: TotalRecallCore) -> Dict[str, Any]:
    """Read-only Hermes profile continuity summary.

    The fleet panel intentionally inspects each profile's own config and Total Recall
    home. It does not open another profile's memory provider, query another agent, or
    merge memory across homes.
    """
    hermes_home = Path(os.getenv("HERMES_HOME") or Path.home() / ".hermes").expanduser()
    profiles = []
    default_config = _read_config_yaml(hermes_home / "config.yaml")
    profiles.append(_profile_fleet_row("default", hermes_home, default_config, core=core))
    profiles_root = hermes_home / "profiles"
    if profiles_root.exists():
        for profile_dir in sorted(path for path in profiles_root.iterdir() if path.is_dir()):
            profiles.append(_profile_fleet_row(profile_dir.name, profile_dir, _read_config_yaml(profile_dir / "config.yaml")))
    federation = _federation_status(core)
    return {
        "ok": True,
        "schema": "total-recall-agent-fleet-v1",
        "mode": "read-only",
        "hermesHome": str(hermes_home),
        "profiles": profiles,
        "isolation": {
            "default": "profile-local",
            "silentSharedMemory": False,
            "detail": "Each profile is inspected separately. Cross-agent memory query requires explicit federation authorization.",
        },
        "federation": federation,
    }


def _profile_fleet_row(name: str, profile_home: Path, config: Dict[str, Any], *, core: TotalRecallCore | None = None) -> Dict[str, Any]:
    memory_cfg = _dict_get(config, "memory", default={})
    provider = str(_config_get(config, "memory", "provider", default="") or "")
    if not provider:
        provider = "total-recall" if _dict_get(memory_cfg, "total-recall", default={}) else "builtin"
    total_cfg = _dict_get(memory_cfg, "total-recall", default={})
    compression_threshold = _config_get(config, "compression", "threshold", default=None)
    auto_cfg = _dict_get(total_cfg, "auto_rehydrate", default={})
    auto_threshold = auto_cfg.get("context_threshold") if isinstance(auto_cfg, dict) else None
    tr_home = _profile_total_recall_home(name, profile_home, total_cfg, core=core)
    latest_checkpoint = _latest_checkpoint_summary(tr_home)
    event_count = _ledger_event_count(tr_home)
    checkpoint_event_count = int(latest_checkpoint.get("eventCount") or 0) if latest_checkpoint else 0
    checkpoint_lag = max(0, event_count - checkpoint_event_count) if latest_checkpoint else event_count
    open_incidents = _open_incident_count(tr_home)
    trust_passed = _trust_gate_passed(tr_home)
    verdict = _fleet_verdict(provider, tr_home.exists(), latest_checkpoint, checkpoint_lag, open_incidents, trust_passed)
    return {
        "profile": name,
        "gateway": _gateway_status(name),
        "memoryProvider": provider or "unknown",
        "memoryIsolation": "profile-local",
        "totalRecallHome": str(tr_home),
        "totalRecallHomeExists": tr_home.exists(),
        "latestCheckpoint": latest_checkpoint.get("name") if latest_checkpoint else None,
        "latestCheckpointPath": latest_checkpoint.get("path") if latest_checkpoint else None,
        "latestCheckpointCreatedAt": latest_checkpoint.get("createdAt") if latest_checkpoint else None,
        "eventCount": event_count,
        "checkpointEventCount": checkpoint_event_count,
        "checkpointLagEvents": checkpoint_lag,
        "openIncidents": open_incidents,
        "compressionThreshold": _safe_float_or_none(compression_threshold),
        "autoRehydrateThreshold": _safe_float_or_none(auto_threshold),
        "autoRehydrateEnabled": _bool_config(auto_cfg.get("enabled", True) if isinstance(auto_cfg, dict) else True),
        "verdict": verdict,
    }


def _profile_total_recall_home(name: str, profile_home: Path, total_cfg: Dict[str, Any], *, core: TotalRecallCore | None = None) -> Path:
    configured = total_cfg.get("home") if isinstance(total_cfg, dict) else None
    if configured:
        return Path(str(configured)).expanduser()
    if core is not None and name == "default" and not (profile_home / "total-recall").exists():
        return core.home
    return profile_home / "total-recall"


def _latest_checkpoint_summary(home: Path) -> Dict[str, Any]:
    checkpoints = sorted((home / "checkpoints").glob("*.json"), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    if not checkpoints:
        return {}
    path = checkpoints[0]
    payload = _read_json_file(path)
    return {
        "name": path.name,
        "path": str(path),
        "eventCount": _safe_int(payload.get("event_count") or payload.get("eventCount") or 0),
        "createdAt": payload.get("created_at") or payload.get("createdAt") or payload.get("timestamp"),
        "label": payload.get("label"),
    }


def _ledger_event_count(home: Path) -> int:
    path = home / "ledger" / "events.jsonl"
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip())
    except Exception:
        return 0


def _open_incident_count(home: Path) -> int:
    count = 0
    for path in (home / "incidents").glob("*.json"):
        payload = _read_json_file(path)
        status = str(payload.get("status") or "OPEN").upper()
        if status not in {"RESOLVED", "CLOSED", "DONE"}:
            count += 1
    return count


def _trust_gate_passed(home: Path) -> bool:
    reports = list((home / "reports").glob("*trust*gate*.json")) + list((home / "reports").glob("trust*.json"))
    for path in sorted(reports, key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
        payload = _read_json_file(path)
        if payload.get("ok") is True or str(payload.get("status") or "").upper() == "PASS":
            return True
    return False


def _fleet_verdict(provider: str, home_exists: bool, checkpoint: Dict[str, Any], lag: int, open_incidents: int, trust_passed: bool) -> str:
    if provider != "total-recall":
        return "Trust Gate needed"
    if not home_exists or not checkpoint or lag > 0:
        return "Save first"
    if open_incidents > 0 or not trust_passed:
        return "Trust Gate needed"
    return "Ready"


def _gateway_status(profile: str) -> str:
    try:
        run = subprocess.run(["pgrep", "-fl", f"hermes.*({re.escape(profile)}|gateway)|gateway.*{re.escape(profile)}"], capture_output=True, text=True, timeout=1, check=False)
    except Exception:
        return "stopped"
    current_pid = str(os.getpid())
    lines = [line for line in (run.stdout or "").splitlines() if current_pid not in line]
    return "running" if lines else "stopped"


def _federation_status(core: TotalRecallCore) -> Dict[str, Any]:
    payload = _guarded_call(core.federation_list)
    targets = payload.get("targets") if payload.get("ok") else []
    targets = targets if isinstance(targets, list) else []
    return {
        "status": "registered" if targets else "isolated",
        "targetCount": len(targets),
        "targets": [{"name": item.get("name"), "role": item.get("role"), "scopes": item.get("scopes") or []} for item in targets],
        "requiresExplicitAuthorization": True,
        "silentSharedMemory": False,
        "detail": "Federation is displayed only as registration metadata. Queries require explicit authorization and return workspace-separated results.",
    }


def _read_config_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        try:
            loaded = json.loads(text)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return _parse_simple_yaml_mapping(text)


def _parse_simple_yaml_mapping(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    stack: list[tuple[int, Dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line or line.startswith("-"):
            continue
        key, value = line.split(":", 1)
        key = key.strip().strip("\"'")
        value = value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root
        if not value:
            child: Dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_simple_yaml_scalar(value)
    return root


def _parse_simple_yaml_scalar(value: str) -> Any:
    value = value.split(" #", 1)[0].strip().strip("\"'")
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except Exception:
        return value


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _config_get(config: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _dict_get(config: Dict[str, Any], key: str, *, default: Any) -> Any:
    value = config.get(key) if isinstance(config, dict) else None
    return value if isinstance(value, dict) else default


def _safe_float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _bool_config(value: Any) -> bool:
    return str(value).lower() not in {"0", "false", "no", "off"}


def _context_risk(core: TotalRecallCore, *, backup_dir: Path, backup: Dict[str, Any] | None = None) -> Dict[str, Any]:
    summary = _summary(core)
    backup_ready = _backup_readiness(core, backup_dir=backup_dir, backup=backup)
    trust = _trust_gate_summary(core)
    lag = int(summary.get("checkpointLagEvents") or 0)
    open_incidents = int(summary.get("openIncidents") or 0)
    checkpoint_current = summary.get("checkpointStatus") == "current"
    trust_ok = trust.get("ok") is True
    backup_ok = backup_ready.get("ok") is True
    safe_restart = checkpoint_current and open_incidents == 0
    safe_compact = safe_restart and trust_ok
    safe_restore = safe_compact and backup_ok
    if safe_restore:
        risk_zone = "READY"
        verdict = "Ready"
        next_action = "Safe to restart or compact. Restore still requires verify after import."
        tone = "ok"
    elif lag:
        risk_zone = "SAVE_FIRST"
        verdict = "Save first"
        next_action = f"Save a restore point: {lag} new memor{'y' if lag == 1 else 'ies'} are not checkpointed."
        tone = "warn"
    elif open_incidents:
        risk_zone = "TRUST_GATE_NEEDED"
        verdict = "Resolve incidents"
        next_action = f"Resolve {open_incidents} open safety incident(s) and run Trust Gate before restart/restore confidence."
        tone = "bad"
    elif not trust_ok:
        risk_zone = "TRUST_GATE_NEEDED"
        verdict = "Needs safety check"
        next_action = "Run Trust Gate before treating compact or restore as safe."
        tone = "warn"
    elif not backup_ok:
        risk_zone = "BACKUP_NEEDED"
        verdict = "Backup needed"
        next_action = "Write a backup after verification so restore has a recovery copy."
        tone = "warn"
    else:
        risk_zone = "TRUST_GATE_NEEDED"
        verdict = "Check memory"
        next_action = "Run Fix All, then refresh this panel."
        tone = "warn"
    return {
        "ok": safe_restore,
        "schema": "total-recall-context-risk-v1",
        "riskZone": risk_zone,
        "verdict": verdict,
        "tone": tone,
        "nextAction": next_action,
        "checkpointLagEvents": lag,
        "checkpointStatus": summary.get("checkpointStatus"),
        "openIncidents": open_incidents,
        "safeToRestart": safe_restart,
        "safeToCompact": safe_compact,
        "safeToRestore": safe_restore,
        "rehydratePreview": _rehydrate_preview(core, summary=summary, trust=trust, backup_ready=backup_ready, risk_zone=risk_zone),
        "actionLadder": _protected_action_ladder(summary=summary, trust=trust, backup_ready=backup_ready, risk_zone=risk_zone),
        "rows": [
            {"name": "Restart readiness", "status": "Ready" if safe_restart else "Save first", "detail": next_action if not safe_restart else "Restore point is current and no open incidents are reported.", "ok": safe_restart},
            {"name": "Compaction readiness", "status": "Ready" if safe_compact else "Run Trust Gate", "detail": "Compaction should have a current restore point and a passing Trust Gate.", "ok": safe_compact},
            {"name": "Restore readiness", "status": "Ready" if safe_restore else "Test-home only", "detail": "Remote backups are transport. Restore into a test home, verify, then run Trust Gate before replacing active memory.", "ok": safe_restore},
            {"name": "Backup coverage", "status": title_case_readiness(backup_ready.get("status")), "detail": backup_ready.get("nextAction") or backup_ready.get("message") or "Backup status unavailable.", "ok": backup_ok},
        ],
    }


def _rehydrate_preview(
    core: TotalRecallCore,
    *,
    summary: Dict[str, Any],
    trust: Dict[str, Any],
    backup_ready: Dict[str, Any],
    risk_zone: str,
) -> Dict[str, Any]:
    checkpoint = summary.get("latestCheckpoint") or "missing"
    anchor = (trust.get("anchorFile") or trust.get("anchor") or "verify/trust gate not run") if isinstance(trust, dict) else "verify/trust gate not run"
    lines = [
        "[Total Recall Rehydrate Preview]",
        "read_only: true",
        f"risk_zone: {risk_zone}",
        f"home: {core.home}",
        f"checkpoint: {checkpoint}",
        f"anchor: {anchor}",
        f"checkpoint_lag_events: {int(summary.get('checkpointLagEvents') or 0)}",
        f"open_incidents: {int(summary.get('openIncidents') or 0)}",
        f"backup_status: {backup_ready.get('status') or 'unknown'}",
        "",
        "Operator note: this is a preview only. It does not write reports, hydrate another agent, or share memory across profiles.",
    ]
    return {
        "ok": True,
        "schema": "total-recall-rehydrate-preview-v1",
        "readOnly": True,
        "riskZone": risk_zone,
        "text": "\n".join(lines),
    }


def _protected_action_ladder(*, summary: Dict[str, Any], trust: Dict[str, Any], backup_ready: Dict[str, Any], risk_zone: str) -> List[Dict[str, Any]]:
    checkpoint_current = summary.get("checkpointStatus") == "current"
    trust_ok = trust.get("ok") is True
    backup_ok = backup_ready.get("ok") is True
    return [
        {
            "name": "Save Restore Point",
            "endpoint": "/api/checkpoint",
            "method": "POST",
            "enabled": True,
            "status": "current" if checkpoint_current else "needed",
            "detail": "Save the current ledger position before restart or compaction.",
        },
        {
            "name": "Verify",
            "endpoint": "/api/verify",
            "method": "POST",
            "enabled": checkpoint_current,
            "status": "available" if checkpoint_current else "save_first",
            "detail": "Verify ledger, checkpoint, and signed anchor before trusting memory.",
        },
        {
            "name": "Trust Gate",
            "endpoint": "/api/trust/verify",
            "method": "POST",
            "enabled": checkpoint_current,
            "status": "pass" if trust_ok else "needed",
            "detail": "Run release-grade continuity checks before restore confidence.",
        },
        {
            "name": "Backup",
            "endpoint": "/api/protection/fix-all",
            "method": "POST",
            "enabled": checkpoint_current and trust_ok,
            "status": "current" if backup_ok else "needed",
            "detail": "Write recovery transport after the memory authority is saved and verified.",
        },
        {
            "name": "Rehydrate Preview",
            "endpoint": "/api/rehydrate-preview",
            "method": "GET",
            "enabled": True,
            "status": risk_zone.lower(),
            "detail": "Generate a read-only context block preview without starting a new session.",
        },
        {
            "name": "Start fresh hydrated Hermes session",
            "endpoint": None,
            "method": None,
            "enabled": False,
            "status": "later",
            "detail": "Intentionally disabled in this read-only panel; add only after explicit approval.",
        },
    ]


def _setup_checklist(core: TotalRecallCore, *, backup_dir: Path, backup: Dict[str, Any] | None = None) -> Dict[str, Any]:
    summary = _summary(core)
    knowledge = _knowledge_summary(core)
    backup_ready = _backup_readiness(core, backup_dir=backup_dir, backup=backup)
    hf = _hf_status(core)
    trust = _trust_gate_summary(core)
    items = [
        _check_item("Total Recall store", core.home.exists(), "Configured", f"Local home: {core.home}"),
        _check_item("Restore point", summary.get("checkpointStatus") == "current", "Configured", "Save a restore point before long work or machine moves."),
        _check_item("Trust Gate", trust.get("ok") is True, "Configured", "Run Trust Gate after major repairs/restores."),
        _check_item("Search Catalog", knowledge.get("ok") is True, "Configured", "Fix All rebuilds searchable/cited derived views."),
        _check_item("Backups", backup_ready.get("backupCount", 0) > 0, "Configured", "Create at least one backup before relying on restore."),
        _check_item("HF CLI", hf.get("hfCliFound") is True, "Configured", "Install Hugging Face CLI for cloud transport."),
        _check_item("HF login", hf.get("loggedIn") is True, "Configured", "Run hf auth login. Token values stay hidden."),
        _check_item("Private HF dataset", bool(hf.get("repoId")), "Configured", "Set TOTAL_RECALL_HF_REPO_ID to a private dataset id."),
        _check_item("Clone passphrase", hf.get("passphrasePresent") is True, "Configured", "Set TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE outside repo/docs/memory."),
        {"name": "Remote MCP", "status": "Planned", "detail": "Keep remote admin disabled until OAuth/scoped serving is implemented.", "ok": False, "tone": "warn"},
    ]
    configured = sum(1 for item in items if item.get("status") == "Configured")
    return {"ok": configured == len(items) - 1, "schema": "total-recall-setup-checklist-v1", "configured": configured, "total": len(items), "items": items}


def _check_item(name: str, ok: bool, good: str, detail: str) -> Dict[str, Any]:
    return {"name": name, "status": good if ok else "Missing", "detail": detail, "ok": ok, "tone": "ok" if ok else "warn"}


def _safe_hf_username(value: str) -> str:
    value = _redacted_line(value).strip()
    if value.lower().startswith("token"):
        return "logged-in"
    return value[:80]


def _redacted_line(value: str) -> str:
    raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(value or ""))
    text = " ".join(raw.split())[:240]
    secret_patterns = [
        r"(?i)\b(authorization)\s*:\s*bearer\s+\S+",
        r"(?i)\b(token|access_token|api[-_]?key|passphrase|password|secret)\s*[:=]\s*\S+",
        r"\bhf_[A-Za-z0-9_\-]{6,}\b",
    ]
    redacted = text
    for pattern in secret_patterns:
        def _replace_secret(match: re.Match[str]) -> str:
            label = match.group(1) if match.lastindex else "secret"
            return f"{label}: [redacted]"

        redacted = re.sub(pattern, _replace_secret, redacted)
    if redacted != text:
        return redacted
    return text


def _hf_instructions(repo_id: str) -> list[Dict[str, str]]:
    return [
        {"step": "1", "title": "Login", "command": "hf auth login", "detail": "Authenticates the HF CLI. The dashboard never shows the token."},
        {"step": "2", "title": "Create private dataset", "command": f"hf repo create {repo_id} --type dataset --private --exist-ok", "detail": "Use a private dataset. HF is only encrypted transport, not memory authority."},
        {"step": "3", "title": "Set local passphrase", "command": "export TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE='long-secret-kept-outside-repo'", "detail": "Use the same passphrase on the restore machine. Do not commit or ledger this value."},
        {"step": "4", "title": "Export and upload clone", "command": f"PYTHONPATH=src .venv/bin/python -m total_recall_core.cli portable-clone export --out-dir ~/total-recall-portable-clones --provider huggingface --repo-id {repo_id} --upload --format json", "detail": "Uploads only encrypted bundle + manifest."},
        {"step": "5", "title": "Restore on test home first", "command": _restore_command(repo_id), "detail": "Verify and Trust Gate must pass before replacing active memory."},
    ]


def _restore_command(repo_id: str) -> str:
    return (
        f"hf download {repo_id} --repo-type dataset --include 'total-recall-portable-clone-*.tar.gz.enc*' "
        "--local-dir ~/total-recall-portable-clones && "
        "export TOTAL_RECALL_HOME=~/total-recall-restored-test && "
        "PYTHONPATH=src .venv/bin/python -m total_recall_core.cli portable-clone restore "
        "~/total-recall-portable-clones/total-recall-portable-clone-*.tar.gz.enc --replace --format json && "
        "PYTHONPATH=src .venv/bin/python -m total_recall_core.cli verify && "
        "PYTHONPATH=src .venv/bin/python -m total_recall_core.cli trust verify --format text"
    )


def title_case_readiness(value: Any) -> str:
    return str(value or "unknown").lower().replace("_", " ").title()


def _safe_backup_path(path: Path, backup_dir: Path) -> bool:
    try:
        resolved = path.resolve()
        root = backup_dir.expanduser().resolve()
        resolved.relative_to(root)
    except Exception:
        return False
    return resolved.is_file() and resolved.name.startswith("total-recall-backup-") and (resolved.name.endswith(".tar.gz") or resolved.name.endswith(".tar.gz.enc"))


def _providers() -> list[Dict[str, Any]]:
    return [
        {"id": "local_folder", "name": "Local folder", "status": "available", "default": True, "note": "Works now. Point backup-dir at any local or synced folder."},
        {"id": "icloud_drive", "name": "iCloud Drive", "status": "available via folder", "default": True, "note": "Works now if backup-dir is inside your iCloud Drive folder."},
        {"id": "google_drive", "name": "Google Drive", "status": "planned", "default": True, "note": "First direct cloud adapter candidate: OAuth + resumable upload + encrypted bundles."},
        {"id": "arweave", "name": "Arweave", "status": "planned encrypted", "default": True, "note": "Durable archive layer: encrypted bundles, permanent receipts, manual approval for upload costs."},
        {"id": "github", "name": "GitHub", "status": "planned encrypted", "default": False, "note": "Good metadata/receipt mirror or private release assets; not primary memory authority."},
        {"id": "huggingface", "name": "Hugging Face Hub", "status": "available encrypted", "default": False, "note": "Portable agent clone storage: encrypted bundle + manifest only, intended for private HF datasets or buckets."},
        {"id": "dropbox", "name": "Dropbox", "status": "planned", "default": False, "note": "OAuth or local Dropbox folder; encrypted upload adapter should store secrets in Keychain."},
        {"id": "s3", "name": "S3-compatible", "status": "planned encrypted", "default": False, "note": "Good for Backblaze/R2/S3 with Keychain-held credentials."},
        {"id": "pinata_ipfs", "name": "Pinata/IPFS", "status": "planned encrypted", "default": False, "note": "Upload encrypted bundles only; IPFS is not private by default."},
    ]


def _remote_sync(core: TotalRecallCore, *, backup_dir: Path, selected: List[str]) -> Dict[str, Any]:
    selected = selected or [provider["id"] for provider in _providers() if provider.get("default")]
    local_sync = core.sync_status(str(backup_dir))
    results = []
    for provider in _providers():
        if provider["id"] not in selected:
            continue
        if provider["id"] in {"local_folder", "icloud_drive"}:
            results.append({"provider": provider, "ok": local_sync.get("relation") == "in_sync", "sync": local_sync})
        else:
            results.append({
                "provider": provider,
                "ok": False,
                "relation": "not_configured",
                "message": "Adapter not implemented yet. Use encrypted local/synced-folder backup for now.",
            })
    return {"ok": True, "selected": selected, "localSync": local_sync, "results": results}


def _remote_upload_result(core: TotalRecallCore, *, backup_dir: Path, selected: List[str], backup: Dict[str, Any]) -> Dict[str, Any]:
    selected = selected or [provider["id"] for provider in _providers() if provider.get("default")]
    results = []
    for provider in _providers():
        if provider["id"] not in selected:
            continue
        if provider["id"] in {"local_folder", "icloud_drive"}:
            results.append({
                "provider": provider,
                "ok": bool(backup.get("ok")),
                "message": "Backup bundle written locally. Cloud sync depends on the selected folder's sync client.",
                "bundle": (backup.get("backup") or {}).get("bundle"),
            })
        else:
            results.append({
                "provider": provider,
                "ok": False,
                "message": "Direct encrypted upload adapter not implemented yet.",
            })
    return {"ok": bool(backup.get("ok")), "backup": backup, "selected": selected, "results": results, "sync": core.sync_status(str(backup_dir))}


def _launchd_plist(*, home: Path, backup_dir: Path, keep: int, keep_days: int | None, hour: int, minute: int) -> str:
    label = "com.total-recall.backup"
    keep_days_arg = f" --keep-days {keep_days}" if keep_days is not None else ""
    command = (
        f"TOTAL_RECALL_HOME={shlex.quote(str(home))} total-recall backup run "
        f"--out-dir {shlex.quote(str(backup_dir.expanduser()))} --keep {keep}{keep_days_arg}"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>{_xml_escape(command)}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>{hour}</integer>
    <key>Minute</key>
    <integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{_xml_escape(str(backup_dir.expanduser() / "total-recall-backup.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{_xml_escape(str(backup_dir.expanduser() / "total-recall-backup.err.log"))}</string>
</dict>
</plist>
"""


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_CONTROL_CENTER_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Total Recall Memory Control Center</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fb;
      --surface: #ffffff;
      --surface-2: #f1f4f8;
      --ink: #111827;
      --muted: #667085;
      --quiet: #98a2b3;
      --line: #d9dee7;
      --line-strong: #b9c2cf;
      --teal: #007f73;
      --teal-ink: #065f56;
      --teal-soft: #e7f6f3;
      --green: #067647;
      --green-soft: #ecfdf3;
      --amber: #a15c07;
      --amber-soft: #fffaeb;
      --red: #b42318;
      --red-soft: #fef3f2;
      --blue: #175cd3;
      --blue-soft: #eff6ff;
      --shadow: 0 18px 50px rgba(16, 24, 40, .08);
      --radius: 8px;
      --nav: 248px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body { margin: 0; min-width: 320px; background: var(--bg); color: var(--ink); }
    button, input, select, textarea { font: inherit; }
    button, a.button {
      appearance: none;
      border: 1px solid var(--line-strong);
      background: var(--surface);
      color: var(--ink);
      min-height: 36px;
      border-radius: 7px;
      padding: 8px 11px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      font-size: 13px;
      line-height: 1;
      font-weight: 720;
      text-decoration: none;
      cursor: pointer;
      transition: border-color .16s ease, background .16s ease, color .16s ease, transform .16s ease;
    }
    button:hover, a.button:hover { border-color: #8d98a8; transform: translateY(-1px); }
    button.primary { background: var(--teal); border-color: var(--teal); color: #fff; }
    button.ghost { background: transparent; }
    button:disabled { opacity: .58; cursor: wait; transform: none; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
    .app-shell { min-height: 100vh; display: grid; grid-template-columns: var(--nav) minmax(0, 1fr); }
    .sidebar {
      position: sticky;
      top: 0;
      height: 100vh;
      background: #101828;
      color: #f8fafc;
      padding: 18px 16px;
      display: flex;
      flex-direction: column;
      gap: 18px;
      border-right: 1px solid #1d2939;
    }
    .brand {
      color: inherit;
      text-decoration: none;
      display: grid;
      grid-template-columns: 38px 1fr;
      gap: 10px;
      align-items: center;
      padding: 4px 2px 16px;
      border-bottom: 1px solid rgba(255,255,255,.12);
    }
    .brand-mark {
      width: 38px;
      height: 38px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      background: #e7f6f3;
      color: #064e48;
      font-weight: 900;
      letter-spacing: 0;
    }
    .brand strong { display: block; font-size: 14px; line-height: 1.1; }
    .brand span:last-child { display: block; margin-top: 3px; font-size: 12px; color: #cbd5e1; }
    .nav { display: grid; gap: 4px; }
    .nav a {
      color: #d0d5dd;
      text-decoration: none;
      min-height: 34px;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 7px 8px;
      border-radius: 7px;
      font-size: 13px;
      font-weight: 680;
    }
    .nav a:hover, .nav a.active { background: rgba(255,255,255,.08); color: #fff; }
    .nav svg, button svg { width: 16px; height: 16px; flex: 0 0 auto; stroke-width: 2; }
    .side-status {
      margin-top: auto;
      border-top: 1px solid rgba(255,255,255,.12);
      padding-top: 14px;
      display: grid;
      gap: 10px;
      color: #d0d5dd;
      font-size: 12px;
    }
    .status-line { display: flex; align-items: center; gap: 8px; }
    #side-home { overflow-wrap: anywhere; word-break: break-word; }
    .status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--amber); box-shadow: 0 0 0 3px rgba(255,255,255,.08); }
    .status-dot.ok { background: #32d583; }
    .status-dot.bad { background: #f97066; }
    .content { min-width: 0; }
    .topbar {
      position: sticky;
      top: 0;
      z-index: 4;
      background: rgba(247,248,251,.94);
      backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--line);
      padding: 18px 24px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: center;
    }
    .title-stack h1 { margin: 0; font-size: 24px; line-height: 1.15; letter-spacing: 0; }
    .title-stack .path { margin-top: 5px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
    .status-strip {
      margin: 18px 24px 0;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      overflow: hidden;
    }
    .status-cell { min-height: 92px; padding: 14px 15px; border-right: 1px solid var(--line); }
    .status-cell:last-child { border-right: 0; }
    .memory-hero {
      margin: 16px 24px 0;
      background: linear-gradient(135deg, #e7f6f3 0%, #ffffff 72%);
      border: 1px solid #86d6cc;
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 18px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: center;
    }
    .memory-hero h2 { margin: 0; font-size: 20px; line-height: 1.2; }
    .memory-hero p { margin: 7px 0 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .memory-hero .hero-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
    .eyeline { color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 820; letter-spacing: .04em; }
    .status-value { margin-top: 8px; font-size: 20px; line-height: 1.1; font-weight: 820; overflow-wrap: anywhere; }
    .status-value.ok { color: var(--green); }
    .status-value.warn { color: var(--amber); }
    .status-value.bad { color: var(--red); }
    .status-sub { margin-top: 6px; color: var(--muted); font-size: 12px; line-height: 1.35; overflow-wrap: anywhere; }
    .workspace {
      padding: 18px 24px 28px;
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 16px;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      min-width: 0;
      overflow: hidden;
    }
    .panel.trust { grid-column: span 7; }
    .panel.knowledge { grid-column: span 5; }
    .panel.workbench { grid-column: span 7; }
    .panel.remote { grid-column: span 5; }
    .panel.context-risk, .panel.loop-inbox { grid-column: span 6; }
    .panel.vault, .panel.backups, .panel.providers, .panel.hf, .panel.hf-wizard, .panel.hf-process, .panel.setup-checklist { grid-column: 1 / -1; }
    .panel-header {
      padding: 15px 16px 13px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }
    .panel-header h2 { margin: 0; font-size: 15px; line-height: 1.2; letter-spacing: 0; }
    .panel-header p { margin: 5px 0 0; color: var(--muted); font-size: 12px; line-height: 1.35; }
    .panel-body { padding: 14px 16px 16px; }
    .row-list { display: grid; gap: 8px; }
    .gate, .data-row {
      min-height: 54px;
      border: 1px solid var(--line);
      border-radius: 7px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      padding: 10px 11px;
      background: #fff;
    }
    .gate strong, .data-row strong { display: block; font-size: 13px; line-height: 1.25; }
    .row-title { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
    .help-mark {
      width: 17px;
      height: 17px;
      border-radius: 50%;
      display: inline-grid;
      place-items: center;
      border: 1px solid var(--line-strong);
      background: #f8fafc;
      color: var(--muted);
      font-size: 11px;
      line-height: 1;
      font-weight: 900;
      cursor: help;
      flex: 0 0 auto;
    }
    .help-mark:hover, .help-mark:focus { color: var(--teal-ink); border-color: #86d6cc; background: var(--teal-soft); outline: none; }
    .help-panel {
      margin: 12px 24px 0;
      border: 1px solid #bfd7ff;
      background: var(--blue-soft);
      color: var(--blue);
      border-radius: var(--radius);
      padding: 12px 14px;
      display: grid;
      gap: 3px;
      font-size: 13px;
      line-height: 1.4;
    }
    .help-panel strong { color: #123e8a; }
    .process-steps { display: grid; gap: 9px; }
    .process-step {
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      padding: 10px 11px;
      display: grid;
      gap: 5px;
    }
    .process-step code, .command-line {
      display: block;
      overflow-x: auto;
      white-space: pre-wrap;
      word-break: break-word;
      border-radius: 6px;
      padding: 8px;
      background: #0b1220;
      color: #e4e7ec;
      font-size: 11px;
      line-height: 1.45;
    }
    .two-col { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .detail { margin-top: 3px; color: var(--muted); font-size: 12px; line-height: 1.35; overflow-wrap: anywhere; }
    .badge {
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 11px;
      line-height: 1;
      font-weight: 820;
      white-space: nowrap;
      border: 1px solid transparent;
    }
    .badge.ok { background: var(--green-soft); color: var(--green); border-color: #abefc6; }
    .badge.warn { background: var(--amber-soft); color: var(--amber); border-color: #fedf89; }
    .badge.bad { background: var(--red-soft); color: var(--red); border-color: #fecdca; }
    .badge.info { background: var(--blue-soft); color: var(--blue); border-color: #bfd7ff; }
    .badge.neutral { background: var(--surface-2); color: #475467; border-color: var(--line); }
    .split-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
    .action-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .query-form { display: grid; gap: 10px; }
    .query-form textarea {
      width: 100%;
      min-height: 82px;
      resize: vertical;
      border: 1px solid var(--line-strong);
      border-radius: 7px;
      padding: 10px 11px;
      color: var(--ink);
      background: #fff;
      font-size: 13px;
      line-height: 1.45;
    }
    .form-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .field {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      min-height: 36px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      padding: 7px 9px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 720;
    }
    .field select, .field input[type="text"], .field input[type="password"] {
      border: 0;
      outline: 0;
      color: var(--ink);
      background: transparent;
      font-size: 13px;
      min-width: 88px;
    }
    .field.grow { flex: 1 1 340px; justify-content: flex-start; }
    .field.grow input[type="text"], .field.grow input[type="password"] { width: 100%; min-width: 220px; }
    .field input[type="checkbox"] { width: 15px; height: 15px; accent-color: var(--teal); }
    .tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
    .tabs button[aria-selected="true"] { background: var(--teal-soft); border-color: #86d6cc; color: var(--teal-ink); }
    .workbench-output {
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fbfcfe;
      min-height: 150px;
      max-height: 520px;
      overflow: auto;
      padding: 12px;
      display: grid;
      gap: 10px;
    }
    .answer {
      color: var(--ink);
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
    }
    .evidence-list { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
    .evidence-list li {
      border-top: 1px solid var(--line);
      padding-top: 7px;
      display: grid;
      gap: 3px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }
    .console {
      grid-column: 1 / -1;
      background: #0b1220;
      border: 1px solid #101828;
      border-radius: var(--radius);
      overflow: hidden;
    }
    .console-header {
      min-height: 42px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      color: #d0d5dd;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(255,255,255,.1);
      font-size: 12px;
      font-weight: 780;
    }
    pre {
      margin: 0;
      color: #e4e7ec;
      background: #0b1220;
      white-space: pre-wrap;
      overflow: auto;
      max-height: 340px;
      padding: 14px;
      font-size: 12px;
      line-height: 1.45;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 10px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .04em; font-weight: 820; }
    td.path { color: var(--muted); overflow-wrap: anywhere; font-size: 12px; }
    tr:last-child td { border-bottom: 0; }
    .provider-grid { display: grid; gap: 8px; }
    .provider-row {
      border: 1px solid var(--line);
      border-radius: 7px;
      min-height: 60px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      padding: 10px 11px;
      align-items: center;
      cursor: pointer;
      background: #fff;
    }
    .provider-title { display: flex; align-items: center; gap: 9px; min-width: 0; }
    .provider-title input { width: 16px; height: 16px; accent-color: var(--teal); flex: 0 0 auto; }
    .hidden { display: none !important; }
    @media (max-width: 1160px) {
      .app-shell { grid-template-columns: 1fr; }
      .sidebar {
        position: relative;
        height: auto;
        display: block;
        padding: 12px 14px;
      }
      .brand { border-bottom: 0; padding-bottom: 8px; }
      .nav { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 8px; }
      .side-status { display: none; }
      .status-strip { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .memory-hero { grid-template-columns: 1fr; }
      .memory-hero .hero-actions { justify-content: flex-start; }
      .status-cell { border-bottom: 1px solid var(--line); }
      .panel.trust, .panel.knowledge, .panel.workbench, .panel.remote, .panel.context-risk, .panel.loop-inbox { grid-column: 1 / -1; }
    }
    @media (max-width: 760px) {
      .topbar { position: relative; grid-template-columns: 1fr; padding: 16px; }
      .toolbar { justify-content: flex-start; }
      .status-strip { margin: 14px 16px 0; grid-template-columns: 1fr; }
      .memory-hero { margin: 14px 16px 0; }
      .status-cell { border-right: 0; }
      .workspace { padding: 14px 16px 22px; grid-template-columns: 1fr; }
      .nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .action-grid { grid-template-columns: 1fr; }
      .gate, .data-row, .provider-row { grid-template-columns: 1fr; }
      .split-actions { justify-content: flex-start; }
      .two-col { grid-template-columns: 1fr; }
      table { font-size: 12px; }
      th:nth-child(3), td:nth-child(3) { display: none; }
    }
    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
    }
  </style>
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="#top" aria-label="Total Recall home">
        <span class="brand-mark">TR</span>
        <span><strong>Total Recall</strong><span>Memory Control Center</span></span>
      </a>
      <nav class="nav" aria-label="Control center">
        <a class="active" href="#top"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 13h7V4H4v9zM13 20h7V4h-7v16zM4 20h7v-5H4v5z"/></svg>Overview</a>
        <a href="#trust"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3l7 3v5c0 4.5-2.8 8.5-7 10-4.2-1.5-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-5"/></svg>Safety Check</a>
        <a href="#knowledge"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 6h16M4 12h16M4 18h10"/><path d="M17 15l3 3-3 3"/></svg>Search Catalog</a>
        <a href="#remote"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 12a7 7 0 0 1 14 0"/><path d="M12 12v8"/><path d="M8 16l4 4 4-4"/></svg>Connections</a>
        <a href="#hf"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 15a4 4 0 0 0 4 4h9a4 4 0 0 0 1-7.9A6 6 0 0 0 7.2 8"/><path d="M12 12v7"/><path d="M9 16l3 3 3-3"/></svg>Continue Elsewhere</a>
        <a href="#context-risk"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 4.3L2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0z"/></svg>Risk Zone</a>
        <a href="#workbench"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 5h16v14H4z"/><path d="M8 9h8M8 13h5"/></svg>Ask Memory</a>
        <a href="#loop-inbox"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 7h14M5 12h10M5 17h14"/><path d="M17 10l3 2-3 2"/></svg>Agent Inbox</a>
        <a href="#vault"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M3 5h7l2 3h9v11H3z"/><path d="M8 13h8"/><path d="M13 10l3 3-3 3"/></svg>Vault</a>
        <a href="#backups"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 7h16v12H4z"/><path d="M8 7V5h8v2"/><path d="M8 12h8"/></svg>Backups</a>
      </nav>
      <div class="side-status">
        <div class="status-line"><span class="status-dot" id="side-dot"></span><span id="side-state">Checking authority</span></div>
        <div id="side-home">Loading store...</div>
      </div>
    </aside>

    <div class="content">
      <header class="topbar" id="top">
        <div class="title-stack">
          <h1>Memory Control Center</h1>
          <div class="path" id="home-path">Loading Total Recall home...</div>
        </div>
        <div class="toolbar">
          <button onclick="refresh()" title="Refresh the dashboard from the local Total Recall store."><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>Refresh</button>
          <button onclick="runAction('/api/checkpoint', 'Save restore point')" title="Save the current memory vault position. This does not rebuild the Search Catalog or run the quality gate."><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>Save Restore Point</button>
          <button class="primary" onclick="runProtectionCycle()" title="One-click maintenance: save/backup, rebuild the Search Catalog, rebuild compiled truth, run the quality gate, then refresh."><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3l7 3v5c0 4.5-2.8 8.5-7 10-4.2-1.5-7-5.5-7-10V6l7-3z"/></svg>Fix All</button>
        </div>
      </header>

      <section class="status-strip" aria-label="System status">
        <div class="status-cell"><div class="eyeline">Memory Vault <span class="help-mark" tabindex="0" title="The append-only Total Recall ledger. This is the authority for saved memory.">?</span></div><div class="status-value" id="metric-authority">-</div><div class="status-sub" id="metric-authority-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Restore Point <span class="help-mark" tabindex="0" title="The latest signed checkpoint and anchor. Saving a restore point protects the current ledger position, but does not rebuild derived search or quality views.">?</span></div><div class="status-value" id="metric-checkpoint">-</div><div class="status-sub" id="metric-checkpoint-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Search Catalog <span class="help-mark" tabindex="0" title="A rebuildable index/graph/compiled-truth layer used for fast cited answers. It can be stale even when the restore point is current.">?</span></div><div class="status-value" id="metric-knowledge">-</div><div class="status-sub" id="metric-knowledge-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Quality Check <span class="help-mark" tabindex="0" title="Short dashboard label for the Release Gate scorecard. NO_EVAL means it has not been run for the current derived state.">?</span></div><div class="status-value" id="metric-score">-</div><div class="status-sub" id="metric-score-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Backups</div><div class="status-value" id="metric-backup">-</div><div class="status-sub" id="metric-backup-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Connections</div><div class="status-value" id="metric-remote">-</div><div class="status-sub" id="metric-remote-sub">-</div></div>
      </section>

      <section class="memory-hero" aria-label="Memory protection summary">
        <div>
          <h2 id="hero-title">Checking memory protection...</h2>
          <p id="hero-sub">Total Recall is checking the memory vault, latest restore point, search catalog, and backups.</p>
        </div>
        <div class="hero-actions">
          <button class="primary" onclick="runProtectionCycle()" title="One-click maintenance: save/backup, rebuild the Search Catalog, rebuild compiled truth, run the quality gate, then refresh.">Fix All</button>
          <button onclick="runAction('/api/checkpoint', 'Save restore point')" title="Save the current memory vault position. Derived Search Catalog rows may still need Fix All or manual rebuilds.">Save Restore Point</button>
          <a class="button" href="#workbench" title="Ask cited questions against verified Total Recall memory.">Ask Memory</a>
        </div>
      </section>

      <section class="help-panel" aria-live="polite">
        <strong id="selected-help-title">Help</strong>
        <span id="selected-help-text">Click any ? mark to see what that selected item means.</span>
      </section>

      <main class="workspace">
        <section class="panel hf" id="hf">
          <div class="panel-header">
            <div>
              <h2>Continue on another machine</h2>
              <p>Encrypted Hugging Face transport status. Trust still comes from restore → verify → Trust Gate.</p>
            </div>
          </div>
          <div class="panel-body">
            <div class="two-col">
              <div class="row-list" id="hf-rows"></div>
              <div class="row-list" id="portable-rows"></div>
            </div>
          </div>
        </section>

        <section class="panel hf-wizard" id="hf-wizard">
          <div class="panel-header">
            <div>
              <h2>Hugging Face Backup Wizard</h2>
              <p>Uploaded is not green. Restorable + verified + trust-gated is green. Active memory was not replaced.</p>
            </div>
            <div class="split-actions">
              <button onclick="refresh()">Refresh status</button>
            </div>
          </div>
          <div class="panel-body">
            <div class="row-list" id="hf-wizard-rows"></div>
            <div class="form-row" style="margin-top: 10px;">
              <label class="field grow">Repo <input id="hf-wizard-repo" type="text" value=""></label>
              <button onclick="runHfWizardAction('/api/hf/repo/validate', 'Validate private dataset')">Validate private dataset</button>
              <button onclick="runHfWizardAction('/api/hf/repo/create', 'Create private dataset')">Create private dataset</button>
            </div>
            <div class="form-row" style="margin-top: 8px;">
              <label class="field grow">Passphrase <input id="hf-wizard-passphrase" type="password" autocomplete="off" value=""></label>
              <button onclick="saveHfPassphrase()">Save passphrase for this session</button>
              <button onclick="runHfWizardAction('/api/hf/session/clear', 'Clear passphrase')">Clear passphrase</button>
            </div>
            <div class="form-row" style="margin-top: 8px;">
              <button class="primary" onclick="runHfWizardAction('/api/hf/export-upload', 'Export encrypted clone and upload')">Export encrypted clone and upload</button>
              <button onclick="runHfWizardAction('/api/hf/restore-test', 'Restore into temporary test home')">Restore into temporary test home</button>
            </div>
          </div>
        </section>

        <section class="panel hf-process" id="hf-process">
          <div class="panel-header">
            <div>
              <h2>Hugging Face process</h2>
              <p>Separate implementation recipe for cloud continuity. Commands are copyable, but destructive restore stays test-home first.</p>
            </div>
          </div>
          <div class="panel-body">
            <div class="process-steps" id="hf-process-steps"></div>
          </div>
        </section>

        <section class="panel context-risk" id="context-risk">
          <div class="panel-header">
            <div>
              <h2>Context / Hydration</h2>
              <p>Current Context Risk Zone, checkpoint lag, restart/compact/restore safety, and read-only rehydrate preview.</p>
            </div>
            <button onclick="showRehydratePreview()">Rehydrate Preview</button>
          </div>
          <div class="panel-body">
            <div class="row-list" id="context-risk-rows"></div>
            <pre class="workbench-output" id="rehydrate-preview">Rehydrate preview will appear here after refresh.</pre>
          </div>
        </section>

        <section class="panel agent-fleet" id="agent-fleet">
          <div class="panel-header">
            <div>
              <h2>Agent Fleet</h2>
              <p>Read-only per-profile continuity state. Cross-agent memory stays isolated unless explicitly authorized.</p>
            </div>
            <button onclick="refresh()">Refresh fleet</button>
          </div>
          <div class="panel-body">
            <div class="row-list" id="agent-fleet-rows"></div>
            <div class="detail" id="agent-federation-note">No silent shared memory. Federation metadata only.</div>
          </div>
        </section>

        <section class="panel protected-ladder" id="protected-ladder">
          <div class="panel-header">
            <div>
              <h2>Protected action ladder</h2>
              <p>Guarded sequence: save, verify, trust gate, backup, preview. Fresh hydrated session remains disabled for now.</p>
            </div>
          </div>
          <div class="panel-body">
            <div class="row-list" id="protected-ladder-rows"></div>
          </div>
        </section>

        <section class="panel loop-inbox" id="loop-inbox">
          <div class="panel-header">
            <div>
              <h2>Agent work inbox</h2>
              <p>Read-only loop status and evidence. This panel does not run agents.</p>
            </div>
          </div>
          <div class="panel-body">
            <div class="row-list" id="loop-rows"></div>
          </div>
        </section>

        <section class="panel setup-checklist" id="setup-checklist">
          <div class="panel-header">
            <div>
              <h2>First-run checklist</h2>
              <p>Configured / missing / planned setup items for safe continuity.</p>
            </div>
          </div>
          <div class="panel-body">
            <div class="row-list" id="setup-rows"></div>
          </div>
        </section>

        <section class="panel trust" id="trust">
          <div class="panel-header">
            <div>
              <h2>Safety Check</h2>
              <p>Shows whether memory is safe to trust, restore, and search.</p>
            </div>
            <div class="split-actions">
              <button onclick="runAction('/api/doctor', 'Doctor')">Doctor</button>
              <button onclick="runAction('/api/verify', 'Verify')">Verify</button>
              <button class="primary" onclick="runAction('/api/trust/verify', 'Trust Gate')">Trust Gate</button>
            </div>
          </div>
          <div class="panel-body">
            <div class="row-list" id="trust-gates"></div>
          </div>
        </section>

        <section class="panel knowledge" id="knowledge">
          <div class="panel-header">
            <div>
              <h2>Search Catalog</h2>
              <p>The searchable catalog built from verified memory. Rebuild it from the vault whenever needed.</p>
            </div>
          </div>
          <div class="panel-body">
            <div class="row-list" id="knowledge-rows"></div>
            <div class="action-grid" style="margin-top: 10px;">
              <button onclick="runKnowledgeAction('/api/knowledge/index/rebuild', 'Knowledge index rebuild')" title="Rebuild the searchable index from the memory vault. Fixes Index: Stale.">Rebuild Index</button>
              <button onclick="runKnowledgeAction('/api/knowledge/graph/rebuild', 'Graph rebuild')" title="Rebuild entity and relationship evidence from the memory vault.">Rebuild Graph</button>
              <button onclick="runKnowledgeAction('/api/knowledge/truth/build', 'Compiled truth build')" title="Rebuild the readable compiled-truth projection from current evidence. Fixes Compiled Truth: STALE.">Build Truth</button>
              <button onclick="runKnowledgeAction('/api/knowledge/evaluate/run', 'Evaluation scorecard')" title="Run the quality/release gate over the Search Catalog and compiled truth. Fixes NO_EVAL.">Run Scorecard</button>
              <button onclick="runKnowledgeAction('/api/knowledge/synthesize/run', 'Derived synthesis')" title="Create provisional learning/summary candidates. This is optional and never rewrites authority silently.">Run Synthesis</button>
              <button onclick="showCompiledTruth()" title="Display the current compiled-truth projection without rebuilding it.">Show Truth</button>
            </div>
          </div>
        </section>

        <section class="panel workbench" id="workbench">
          <div class="panel-header">
            <div>
              <h2>Ask Memory</h2>
              <p>Ask questions, inspect evidence, and check whether remembered facts are still current.</p>
            </div>
          </div>
          <div class="panel-body">
            <div class="tabs" role="tablist" aria-label="Workbench mode">
              <button id="tab-query" aria-selected="true" onclick="switchWorkbench('query')">Query</button>
              <button id="tab-graph" aria-selected="false" onclick="switchWorkbench('graph')">Graph</button>
              <button id="tab-freshness" aria-selected="false" onclick="switchWorkbench('freshness')">Freshness</button>
              <button id="tab-source" aria-selected="false" onclick="switchWorkbench('source')">Source</button>
              <button id="tab-truth" aria-selected="false" onclick="switchWorkbench('truth')">Truth</button>
            </div>
            <div id="query-pane" class="query-form">
              <textarea id="query-text">storefront demand trust payments fulfillment brand promise</textarea>
              <div class="form-row">
                <label class="field">Mode <select id="query-mode"><option>normal</option><option>fast</option><option>strict</option><option>explore</option></select></label>
                <label class="field"><input type="checkbox" id="scope-private" checked>Private</label>
                <label class="field"><input type="checkbox" id="scope-public" checked>Public</label>
                <button class="primary" onclick="runKnowledgeQuery()">Run Query</button>
              </div>
            </div>
            <div id="graph-pane" class="query-form hidden">
              <div class="form-row">
                <label class="field">Entity <input id="graph-entity" type="text" value="promise"></label>
                <label class="field">As of <input id="graph-at-time" type="text" value=""></label>
                <button class="primary" onclick="inspectGraph()">Inspect Graph</button>
                <button onclick="inspectTimeline()">Timeline</button>
              </div>
            </div>
            <div id="freshness-pane" class="query-form hidden">
              <div class="form-row">
                <label class="field grow">Entity <input id="freshness-entity" type="text" value="brand promise"></label>
                <label class="field">Category <select id="freshness-category"><option value="">all</option><option>promise</option><option>decision</option><option>customer</option><option>policy</option><option>project-state</option><option>task</option><option>memory</option></select></label>
                <label class="field">As of <input id="freshness-at-time" type="text" value=""></label>
                <button class="primary" onclick="runFreshness()">Check</button>
              </div>
            </div>
            <div id="source-pane" class="query-form hidden">
              <div class="form-row">
                <label class="field">Type <select id="source-type"><option>meeting</option><option>email</option><option>slack</option><option>github</option><option>crm</option><option>ticket</option><option>calendar</option><option>agent_transcript</option></select></label>
                <label class="field grow">Title <input id="source-title" type="text" value="Working Context"></label>
                <label class="field">Occurred <input id="source-occurred-at" type="text" value=""></label>
                <label class="field">Scope <select id="source-scope"><option>private</option><option>internal</option><option>public</option></select></label>
              </div>
              <textarea id="source-text">Decision: Brand promise is seven-day fulfillment.</textarea>
              <div class="form-row">
                <label class="field grow">Participants <input id="source-participants" type="text" value=""></label>
                <button class="primary" onclick="runSourceIngest()">Ingest Source</button>
              </div>
            </div>
            <div id="truth-pane" class="query-form hidden">
              <div class="form-row">
                <button class="primary" onclick="showCompiledTruth()">Load Compiled Truth</button>
                <button onclick="runKnowledgeAction('/api/knowledge/truth/build', 'Compiled truth build')">Rebuild Truth</button>
              </div>
            </div>
            <div class="workbench-output" id="workbench-output">
              <div class="detail">Run a query, inspect an entity, or load compiled truth.</div>
            </div>
          </div>
        </section>

        <section class="panel remote" id="remote">
          <div class="panel-header">
            <div>
              <h2>Connection Readiness</h2>
              <p>Local memory control works now. Remote/admin connections stay guarded until explicitly enabled.</p>
            </div>
          </div>
          <div class="panel-body">
            <div class="row-list" id="mcp-rows"></div>
          </div>
        </section>

        <section class="panel vault" id="vault">
          <div class="panel-header">
            <div>
              <h2>Obsidian Vault Export</h2>
              <p>Generate a local wikilinked vault from verified Total Recall memory.</p>
            </div>
            <button class="primary" onclick="runVaultExport()">Export Vault</button>
          </div>
          <div class="panel-body">
            <div class="query-form">
              <div class="form-row">
                <label class="field grow">Folder <input id="vault-path" type="text" value=""></label>
                <label class="field">Max events <input id="vault-max-events" type="text" value="500"></label>
                <label class="field">Max entities <input id="vault-max-entities" type="text" value="100"></label>
                <label class="field"><input type="checkbox" id="vault-force">Replace folder</label>
              </div>
              <div class="form-row">
                <label class="field grow">Edited note <input id="vault-import-note" type="text" value=""></label>
                <label class="field">Preview id <input id="vault-preview-id" type="text" value=""></label>
                <label class="field">Proposal ids <input id="vault-proposal-ids" type="text" value=""></label>
                <button onclick="runVaultImportPreview()">Preview Import</button>
                <button onclick="runVaultImportPromote()">Promote</button>
              </div>
              <div class="detail" id="vault-help">The vault is derived. Total Recall ledger, checkpoints, and anchors remain the authority.</div>
              <div class="row-list" id="vault-output"></div>
            </div>
          </div>
        </section>

        <section class="panel providers" id="providers-panel">
          <div class="panel-header">
            <div>
              <h2>Remote Backup Providers</h2>
              <p>Encrypted bundle targets and direct adapter readiness.</p>
            </div>
            <div class="split-actions">
              <button onclick="runRemoteSync()">Sync Check</button>
              <button class="primary" onclick="runRemoteUpload()">Upload Selected</button>
            </div>
          </div>
          <div class="panel-body">
            <div class="provider-grid" id="providers"></div>
          </div>
        </section>

        <section class="panel backups" id="backups">
          <div class="panel-header">
            <div>
              <h2>Memory Protection</h2>
              <p>Shows whether the latest backup covers the same memory point you are using now. Save before long sessions; restore only after verify.</p>
            </div>
            <div class="split-actions">
              <button class="primary" onclick="runProtectionCycle()" title="One-click maintenance: save/backup, rebuild the Search Catalog, rebuild compiled truth, run the quality gate, then refresh.">Fix All</button>
              <a class="button" href="/api/launchd.plist" target="_blank">View LaunchAgent</a>
            </div>
          </div>
          <div class="panel-body">
            <div class="row-list" id="protection-rows" style="margin-bottom: 12px;"></div>
            <table>
              <thead><tr><th>File</th><th>Size</th><th>Modified</th><th>Actions</th></tr></thead>
              <tbody id="backup-rows"><tr><td colspan="4">Loading...</td></tr></tbody>
            </table>
          </div>
        </section>

        <section class="console" aria-label="Activity console">
          <div class="console-header">
            <span id="console-title">Activity Console</span>
            <button class="ghost" onclick="clearConsole()">Clear</button>
          </div>
          <pre id="raw-json">Ready.</pre>
        </section>
      </main>
    </div>
  </div>

  <script>
    let lastStatus = null;
    const $ = id => document.getElementById(id);

    async function getJson(url, options) {
      const response = await fetch(url, options || {});
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || response.statusText);
      }
      return payload;
    }

    async function postJson(url, payload) {
      return getJson(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload || {}),
      });
    }

    async function refresh() {
      try {
        const data = await getJson('/api/status');
        lastStatus = data;
        renderStatus(data);
        renderTrust(data);
        renderKnowledge(data.knowledge || {});
        renderMcp(data.mcp || {});
        renderProtection(data);
        renderProviders(data.providers || []);
        renderHf(data.hf || {}, data.portable || {});
        renderHfWizard(data.hfWizard || {});
        renderHfProcess(data.hf || {});
        renderContextRisk(data.contextRisk || {});
        renderAgentFleet(data.agentFleet || {});
        renderProtectedLadder((data.contextRisk || {}).actionLadder || []);
        renderLoopInbox(data.loops || {});
        renderSetupChecklist(data.setupChecklist || {});
        renderBackups((data.backup || {}).backups || []);
        renderVaultPolicy(data.policy || {});
      } catch (error) {
        renderRaw('Refresh failed', {ok: false, error: String(error.message || error)});
      }
    }

    function renderStatus(data) {
      const summary = data.summary || {};
      const health = data.health || {};
      const knowledge = data.knowledge || {};
      const backup = data.backup || {};
      const backupReadiness = data.backupReadiness || {};
      const checkpointStatus = summary.checkpointStatus || 'missing';
      const authorityOk = !!health.ok && !summary.openIncidents;
      const knowledgeOk = !!knowledge.ok;
      const scorecard = ((knowledge.scorecard || {}).scorecard) || {};
      const score = typeof scorecard.score === 'number' ? `${scorecard.score}/10` : 'No eval';

      $('home-path').textContent = (data.policy || {}).home || 'Unknown home';
      $('side-home').textContent = (data.policy || {}).home || 'Unknown home';
      $('side-state').textContent = authorityOk ? 'Memory protected' : 'Attention required';
      $('side-dot').className = 'status-dot ' + (authorityOk ? 'ok' : 'bad');

      const lag = Number(summary.checkpointLagEvents || 0);
      const heroOk = authorityOk && checkpointStatus === 'current' && backupReadiness.ok === true;
      $('hero-title').textContent = heroOk
        ? 'Memory is protected'
        : lag > 0
          ? `${lag} new memor${lag === 1 ? 'y needs' : 'ies need'} saving`
          : 'Memory needs a safety check';
      $('hero-sub').textContent = heroOk
        ? 'The vault is verified, the restore point is current, and the latest backup covers this memory point.'
        : (backupReadiness.nextAction || 'Click Fix All to save a restore point, verify memory, and back it up.');

      setMetric('metric-authority', authorityOk ? 'Protected' : 'Attention', authorityOk ? 'ok' : 'bad', `${summary.eventCount || 0} saved memory event(s)`);
      setMetric('metric-checkpoint', checkpointStatus === 'current' ? 'Current' : 'Needs saving', checkpointStatus === 'current' ? 'ok' : 'warn', checkpointStatus === 'stale' ? `${lag} new memor${lag === 1 ? 'y' : 'ies'} since restore point` : 'restore-point freshness');
      setMetric('metric-knowledge', knowledgeOk ? 'Ready' : 'Needs rebuild', knowledgeOk ? 'ok' : 'warn', knowledgeSummary(knowledge));
      setMetric('metric-score', score, scorecard.ok ? 'ok' : 'warn', scorecard.status || ((knowledge.scorecard || {}).status || 'quality status'));
      setMetric('metric-backup', backupMetricValue(backupReadiness, backup), backupReadiness.tone || (backup.count ? 'ok' : 'warn'), backupReadiness.nextAction || (backup.latest ? backup.latest.split('/').pop() : 'no backup yet'));
      setMetric('metric-remote', 'Local', 'warn', 'remote admin connections are planned');
    }

    function setMetric(id, value, tone, sub) {
      $(id).textContent = value || '-';
      $(id).className = 'status-value ' + (tone || '');
      $(`${id}-sub`).textContent = sub || '';
    }

    function renderTrust(data) {
      const summary = data.summary || {};
      const health = data.health || {};
      const index = data.index || {};
      const knowledge = data.knowledge || {};
      const backup = data.backup || {};
      const trustGate = data.trustGate || {};
      const backupReadiness = data.backupReadiness || {};
      const checkpointStatus = summary.checkpointStatus || 'missing';
      const rows = [
        gate('Memory vault', !!health.ok, health.ok ? 'Readable and chained' : 'Verify before trusting restored memory'),
        gate('Restore point', checkpointStatus === 'current', checkpointStatus === 'current' ? 'No new memories waiting to be saved' : `${summary.checkpointLagEvents || 0} new memor${Number(summary.checkpointLagEvents || 0) === 1 ? 'y' : 'ies'} since latest restore point`),
        gate('Full safety check', trustGate.ok === true, trustGateSummary(trustGate)),
        gate('Safety incidents', !summary.openIncidents, summary.openIncidents ? `${summary.openIncidents} open safety incident(s)` : 'No open incidents'),
        gate('Search catalog', !!index.fresh, index.fresh ? 'Search catalog is current' : 'Will rebuild from the memory vault when needed'),
        gate('Cited answers', !!knowledge.ok, knowledge.ok ? 'Answers are gated by citations' : knowledge.error || knowledge.status || 'Search layer needs attention'),
        gate('Backup coverage', backupReadiness.ok === true, backupReadiness.message || (backup.count ? `${backup.count} backup archive(s) available` : 'Click Fix All to create the first backup')),
      ];
      $('trust-gates').innerHTML = rows.join('');
    }

    function renderKnowledge(knowledge) {
      const index = knowledge.index || {};
      const graph = knowledge.graph || {};
      const truth = knowledge.compiledTruth || {};
      const synthesis = knowledge.synthesis || {};
      const scorecard = ((knowledge.scorecard || {}).scorecard) || {};
      const rows = [
        dataRow('Index', index.fresh ? 'Fresh' : index.exists ? 'Stale' : 'Missing', `${index.sourceCount || 0} source(s), ${index.quarantineCount || 0} quarantined`, !!index.fresh),
        dataRow('Graph', graph.status || 'MISSING', `${graph.entityCount || 0} entities, ${graph.edgeCount || 0} edges, ${graph.uncitedActiveItems || 0} uncited`, graph.uncitedActiveItems === 0 && graph.status !== 'MISSING'),
        dataRow('Compiled Truth', truth.status || 'NO_PROJECTION', truth.projectionHash || truth.error || 'derived projection', truth.status === 'PASS'),
        dataRow('Synthesis', synthesis.status || 'NO_SYNTHESIS', synthesis.latest || synthesis.error || 'provisional by design', synthesis.status === 'PASS' || synthesis.status === 'NO_SYNTHESIS'),
        dataRow('Release Gate', scorecard.status || ((knowledge.scorecard || {}).status || 'NO_EVAL'), typeof scorecard.score === 'number' ? `${scorecard.score}/10, ${(scorecard.checks || []).length} check(s)` : 'Run scorecard to refresh', !!scorecard.ok),
      ];
      $('knowledge-rows').innerHTML = rows.join('');
    }

    function trustGateSummary(trustGate) {
      if (!trustGate || trustGate.status === 'NO_TRUST_GATE') return 'Run Trust Gate to prove execution checks';
      const summary = trustGate.summary || {};
      return `${trustGate.status || 'UNKNOWN'} | ${summary.passed || 0}/${summary.totalChecks || 0} check(s) passed`;
    }

    function renderMcp(mcp) {
      const rows = (mcp.controls || []).map(item => {
        const tone = item.status === 'implemented' ? 'ok' : item.status === 'guarded' ? 'info' : 'warn';
        return dataRow(item.name, item.status, item.detail, item.status === 'implemented', tone);
      });
      $('mcp-rows').innerHTML = rows.join('');
    }

    function renderProtection(data) {
      const readiness = data.backupReadiness || {};
      const backup = data.backup || {};
      const latestDetail = readiness.latestName
        ? `${readiness.latestName} · ${readiness.latestAgeLabel || 'unknown age'} · ${formatBytes(readiness.totalBytes || backup.totalBytes || 0)} total`
        : 'No backup archive found.';
      const coverageDetail = readiness.archiveEventCount == null
        ? `${readiness.localEventCount || 0} local event(s); archive checkpoint unavailable.`
        : `${readiness.localEventCount || 0} local event(s), ${readiness.archiveEventCount || 0} archived, ${readiness.eventsNotBackedUp || 0} not backed up.`;
      const rehydrateDetail = readiness.rehydrateReady
        ? 'Safe to restore from Total Recall: memory vault verified, restore point current, no open incidents.'
        : `Hold restore until restore point=${readiness.checkpointStatus || 'unknown'}, unsaved memories=${readiness.checkpointLagEvents || 0}, open incidents=${readiness.openIncidents || 0}.`;
      const rows = [
        dataRow('Overall memory protection', titleCaseStatus(readiness.status || 'CHECK'), readiness.nextAction || 'Click Fix All, then refresh.', readiness.ok === true, readiness.tone || 'warn'),
        dataRow('Latest backup', readiness.latestName ? 'Present' : 'Missing', latestDetail, !!readiness.latestName, readiness.latestName ? 'ok' : 'warn'),
        dataRow('Backup coverage', readiness.relation || 'unknown', `${readiness.message || 'Backup relation unavailable.'} ${coverageDetail}`, readiness.relation === 'in_sync', readiness.relation === 'in_sync' ? 'ok' : 'warn'),
        dataRow('Restore guard', readiness.rehydrateReady ? 'Ready' : 'Save first', readiness.compactionRule || rehydrateDetail, readiness.rehydrateReady === true, readiness.rehydrateReady ? 'ok' : 'warn'),
      ];
      $('protection-rows').innerHTML = rows.join('');
    }

    function backupMetricValue(readiness, backup) {
      if (readiness && readiness.status === 'CURRENT') return 'Current';
      if (readiness && readiness.status === 'BACKUP_BEHIND') return 'Behind';
      if (readiness && readiness.status === 'NO_BACKUP') return 'None';
      if (readiness && readiness.status) return titleCaseStatus(readiness.status);
      return backup.count ? String(backup.count) : 'None';
    }

    function titleCaseStatus(value) {
      return String(value || '').toLowerCase().split('_').map(titleCase).join(' ');
    }

    function renderProviders(providers) {
      $('providers').innerHTML = providers.map(provider => `
        <label class="provider-row">
          <span>
            <span class="provider-title">
              <input type="checkbox" name="remote-provider" value="${escapeHtml(provider.id)}" ${provider.default ? 'checked' : ''}>
              <strong>${escapeHtml(provider.name)}</strong>
            </span>
            <span class="detail">${escapeHtml(provider.note)}</span>
          </span>
          ${badge(escapeHtml(provider.status), providerTone(provider.status))}
        </label>
      `).join('');
    }

    function renderHf(hf, portable) {
      const user = hf.loggedIn ? (hf.username || 'Logged in') : 'Not logged in';
      const repo = hf.repoId || 'Not selected';
      const token = hf.tokenSource === 'missing' ? 'Missing' : hf.tokenSource;
      const passphrase = hf.passphrasePresent ? 'Present' : 'Missing';
      $('hf-rows').innerHTML = [
        dataRow('HF CLI', hf.hfCliFound ? 'Configured' : 'Missing', hf.hfCliPath || 'Install Hugging Face CLI to use encrypted cloud transport.', !!hf.hfCliFound, hf.hfCliFound ? 'ok' : 'warn'),
        dataRow('HF login', user, hf.detail || 'Token values are never shown here.', !!hf.loggedIn, hf.loggedIn ? 'ok' : 'warn'),
        dataRow('HF dataset', repo, 'Use a private dataset for encrypted clone bundles and manifests.', !!hf.repoId, hf.repoId ? 'ok' : 'warn'),
        dataRow('HF token visibility', 'Hidden', `Auth source: ${token}. Values are never returned by the API or rendered in the UI.`, true, 'info'),
        dataRow('Clone passphrase', passphrase, 'Only presence is shown. The passphrase must stay outside repo, docs, ledger, and reports.', !!hf.passphrasePresent, hf.passphrasePresent ? 'ok' : 'warn'),
      ].join('');
      const latest = portable.latestClone || {};
      const ledger = latest.ledger || {};
      $('portable-rows').innerHTML = [
        dataRow('Latest encrypted clone', latest.cloneId || 'None found', latest.createdAt || 'Create an encrypted clone before moving machines.', !!latest.cloneId, latest.cloneId ? 'ok' : 'warn'),
        dataRow('Clone ledger point', ledger.eventCount == null ? 'Unknown' : `${ledger.eventCount} events`, ledger.lastEventHash || 'No clone manifest loaded.', ledger.eventCount != null, ledger.eventCount != null ? 'ok' : 'warn'),
        dataRow('Restore default', 'Test home first', 'Restore into ~/total-recall-restored-test, then verify and Trust Gate before replacing active memory.', true, 'info'),
      ].join('');
    }

    function renderHfProcess(hf) {
      const steps = hf.instructions || [];
      $('hf-process-steps').innerHTML = steps.length ? steps.map(step => `
        <div class="process-step">
          <strong>${escapeHtml(step.step)}. ${escapeHtml(step.title)}</strong>
          <span class="detail">${escapeHtml(step.detail)}</span>
          <code>${escapeHtml(step.command)}</code>
        </div>
      `).join('') : '<div class="detail">Hugging Face instructions unavailable.</div>';
    }

    function renderHfWizard(wizard) {
      const repo = wizard.repo || {};
      const session = wizard.session || {};
      const lastExport = wizard.lastExport || {};
      const lastRestore = wizard.lastRestoreTest || {};
      if ($('hf-wizard-repo') && !$('hf-wizard-repo').value && repo.repoId) $('hf-wizard-repo').value = repo.repoId;
      const finalStatus = wizard.readyForGreen ? 'Remote Backup Verified' : 'not green yet';
      $('hf-wizard-rows').innerHTML = [
        dataRow('Step 1: HF Auth', ((wizard.hf || {}).loggedIn ? 'Logged in' : 'Needs login'), 'Token values are hidden; HF remote is encrypted transport only, not authority.', !!((wizard.hf || {}).loggedIn), ((wizard.hf || {}).loggedIn ? 'ok' : 'warn')),
        dataRow('Step 2: Private Dataset', repo.status || 'not_validated', repo.private === true ? 'Repo verified private.' : 'Repo must be private before upload can be green.', repo.private === true, repo.private === true ? 'ok' : 'warn'),
        dataRow('Step 3: Encryption Passphrase', session.passphrasePresent ? 'Present' : 'Missing', 'Stored in this dashboard process only; never echoed.', !!session.passphrasePresent, session.passphrasePresent ? 'ok' : 'warn'),
        dataRow('Step 4: Export + Upload', lastExport.status || 'Not run', lastExport.cloneId || 'Upload success alone does not make this green.', !!lastExport.ok, lastExport.ok ? 'info' : 'warn'),
        dataRow('Step 5: Restore Test', lastRestore.status || 'Not run', lastRestore.testHome ? `Restored copy: ${lastRestore.testHome}` : 'Fresh remote download + temp restore + verify + trust gate required.', !!lastRestore.ok, lastRestore.ok ? 'ok' : 'warn'),
        dataRow('Final', finalStatus, (wizard.activeRestore || {}).reason || 'Active memory was not replaced.', wizard.readyForGreen === true, wizard.readyForGreen ? 'ok' : 'warn'),
      ].join('');
    }

    async function saveHfPassphrase() {
      const passphrase = $('hf-wizard-passphrase').value || '';
      $('hf-wizard-passphrase').value = '';
      await runActionPayload('/api/hf/session/passphrase', 'Save passphrase for this session', {passphrase});
    }

    async function runHfWizardAction(url, label) {
      const repoId = $('hf-wizard-repo') ? $('hf-wizard-repo').value : '';
      await runActionPayload(url, label, {repoId});
    }

    async function runActionPayload(url, label, payload) {
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload || {}),
        });
        const result = await response.json();
        renderRaw(label, result);
        await refresh();
      } catch (error) {
        renderRaw(label, {ok: false, error: String(error.message || error)});
      }
    }

    function renderContextRisk(risk) {
      const rows = (risk.rows || []).map(row => dataRow(row.name, row.status, row.detail, !!row.ok, row.ok ? 'ok' : 'warn'));
      rows.unshift(dataRow('Current Context Risk Zone', risk.riskZone || risk.verdict || 'Checking', risk.nextAction || 'Run Fix All, then refresh.', risk.ok === true, risk.tone || 'warn'));
      rows.push(dataRow('Checkpoint lag', `${risk.checkpointLagEvents || 0} event(s)`, risk.checkpointStatus || 'unknown', Number(risk.checkpointLagEvents || 0) === 0, Number(risk.checkpointLagEvents || 0) === 0 ? 'ok' : 'warn'));
      rows.push(dataRow('Safe to restart?', risk.safeToRestart ? 'Yes' : 'No', risk.safeToRestart ? 'Restore point is current and no incidents are open.' : 'Save/verify first.', !!risk.safeToRestart, risk.safeToRestart ? 'ok' : 'warn'));
      rows.push(dataRow('Safe to compact?', risk.safeToCompact ? 'Yes' : 'No', risk.safeToCompact ? 'Trust Gate passed.' : 'Run Trust Gate before compaction.', !!risk.safeToCompact, risk.safeToCompact ? 'ok' : 'warn'));
      rows.push(dataRow('Safe to restore?', risk.safeToRestore ? 'Yes' : 'No', risk.safeToRestore ? 'Backup coverage is current.' : 'Restore only into a test home until backup/verify/trust are green.', !!risk.safeToRestore, risk.safeToRestore ? 'ok' : 'warn'));
      $('context-risk-rows').innerHTML = rows.join('');
      $('rehydrate-preview').textContent = ((risk.rehydratePreview || {}).text) || 'No rehydrate preview available yet.';
    }

    function renderAgentFleet(fleet) {
      const profiles = fleet.profiles || [];
      const rows = profiles.map(profile => {
        const detail = [
          `gateway ${profile.gateway || 'unknown'}`,
          `provider ${profile.memoryProvider || 'unknown'}`,
          `TR home ${profile.totalRecallHomeExists ? 'exists' : 'missing'}`,
          `checkpoint ${profile.latestCheckpoint || 'none'}`,
          `lag ${profile.checkpointLagEvents || 0}`,
          `incidents ${profile.openIncidents || 0}`,
          `compression ${profile.compressionThreshold == null ? '-' : profile.compressionThreshold}`,
          `auto-rehydrate ${profile.autoRehydrateThreshold == null ? '-' : profile.autoRehydrateThreshold}`,
        ].join(' · ');
        const ready = profile.verdict === 'Ready';
        const tone = ready ? 'ok' : profile.verdict === 'Save first' ? 'warn' : 'info';
        return dataRow(`Profile: ${profile.profile || 'unknown'}`, profile.verdict || 'Check', detail, ready, tone);
      });
      $('agent-fleet-rows').innerHTML = rows.length ? rows.join('') : dataRow('Agent Fleet', 'No profiles', 'No Hermes profiles discovered.', false, 'warn');
      const federation = fleet.federation || {};
      $('agent-federation-note').textContent = federation.detail || 'No silent shared memory; federation requires explicit authorization.';
    }

    function renderProtectedLadder(steps) {
      $('protected-ladder-rows').innerHTML = (steps || []).map(step => {
        const label = step.enabled === false ? `${step.status || 'disabled'} · disabled` : (step.status || 'ready');
        return dataRow(step.name, label, step.detail || '', step.enabled !== false, step.enabled === false ? 'neutral' : 'info');
      }).join('') || dataRow('Protected action ladder', 'Unavailable', 'Refresh status first.', false, 'warn');
    }

    async function showRehydratePreview() {
      try {
        const data = await getJson('/api/rehydrate-preview');
        $('rehydrate-preview').textContent = data.text || JSON.stringify(data, null, 2);
        renderRaw('Rehydrate Preview', data);
      } catch (error) {
        renderRaw('Rehydrate Preview failed', {ok: false, error: String(error.message || error)});
      }
    }

    function renderLoopInbox(inbox) {
      const loops = inbox.loops || [];
      if (!loops.length) {
        $('loop-rows').innerHTML = dataRow('Active loops', 'None', inbox.detail || 'No active evidence loops found.', true, 'ok');
        return;
      }
      $('loop-rows').innerHTML = loops.slice(0, 8).map(loop => {
        const detail = [loop.agent || 'unknown agent', loop.project || 'no project', loop.updated_at || loop.created_at || 'unknown time'].join(' · ');
        const phase = loop.phase || loop.status || 'active';
        const goal = `${loop.goal || loop.loop_id || 'Loop'} — ${detail}`;
        return dataRow('Loop evidence', phase, goal, loop.status === 'active', loop.status === 'active' ? 'info' : 'neutral');
      }).join('');
    }

    function renderSetupChecklist(checklist) {
      const items = checklist.items || [];
      $('setup-rows').innerHTML = items.length ? items.map(item => dataRow(item.name, item.status, item.detail, item.ok === true, item.tone || (item.ok ? 'ok' : 'warn'))).join('') : dataRow('Setup checklist', 'Unavailable', 'Refresh after the local store is ready.', false, 'warn');
    }

    function renderBackups(backups) {
      const rows = backups.map(item => {
        const encoded = encodeURIComponent(item.path);
        return `<tr>
          <td class="path">${escapeHtml(item.path)}</td>
          <td>${formatBytes(item.bytes)}</td>
          <td>${escapeHtml(item.modified)}</td>
          <td><div class="split-actions"><a class="button" href="/api/backups/download?path=${encoded}">Download</a><button onclick="copyText('${escapeJs(item.path)}')">Copy Path</button></div></td>
        </tr>`;
      });
      $('backup-rows').innerHTML = rows.length ? rows.join('') : '<tr><td colspan="4">No backups yet.</td></tr>';
    }

    function renderVaultPolicy(policy) {
      if (!$('vault-path').value) {
        $('vault-path').value = policy.defaultVaultDir || '';
      }
    }

    async function runAction(url, label) {
      setBusy(true);
      renderRaw(label, {status: 'RUNNING'});
      try {
        const data = await getJson(url, {method: 'POST'});
        renderRaw(label, data);
        await refresh();
      } catch (error) {
        renderRaw(label, {ok: false, error: String(error.message || error)});
      } finally {
        setBusy(false);
      }
    }

    async function runProtectionCycle() {
      return runAction('/api/protection/fix-all', 'Fix All');
    }

    async function runKnowledgeAction(url, label) {
      return runAction(url, label);
    }

    async function runVaultExport() {
      const payload = {
        path: $('vault-path').value,
        force: $('vault-force').checked,
        maxEvents: Number($('vault-max-events').value || 500),
        maxEntities: Number($('vault-max-entities').value || 100),
      };
      setBusy(true);
      $('vault-output').innerHTML = dataRow('Vault export', 'Running', 'Generating Obsidian-compatible notes...', false, 'warn');
      try {
        const data = await postJson('/api/vault/export', payload);
        renderVaultResult(data);
        renderRaw('Obsidian vault export', data);
        await refresh();
      } catch (error) {
        const payload = {ok: false, error: String(error.message || error)};
        renderVaultResult(payload);
        renderRaw('Obsidian vault export failed', payload);
      } finally {
        setBusy(false);
      }
    }

    async function runVaultImportPreview() {
      const payload = {
        vault: $('vault-path').value,
        notes: $('vault-import-note').value,
      };
      setBusy(true);
      $('vault-output').innerHTML = dataRow('Vault import preview', 'Running', 'Reading edited notes...', false, 'warn');
      try {
        const data = await postJson('/api/vault/import-preview', payload);
        if (data.preview_id) $('vault-preview-id').value = data.preview_id;
        renderVaultImportResult(data);
        renderRaw('Obsidian import preview', data);
      } catch (error) {
        const payload = {ok: false, error: String(error.message || error)};
        renderVaultImportResult(payload);
        renderRaw('Obsidian import preview failed', payload);
      } finally {
        setBusy(false);
      }
    }

    async function runVaultImportPromote() {
      const payload = {
        previewId: $('vault-preview-id').value,
        proposalIds: $('vault-proposal-ids').value,
      };
      setBusy(true);
      $('vault-output').innerHTML = dataRow('Vault import promote', 'Running', 'Writing approved ledger events...', false, 'warn');
      try {
        const data = await postJson('/api/vault/import-promote', payload);
        renderVaultImportResult(data);
        renderRaw('Obsidian import promote', data);
        await refresh();
      } catch (error) {
        const payload = {ok: false, error: String(error.message || error)};
        renderVaultImportResult(payload);
        renderRaw('Obsidian import promote failed', payload);
      } finally {
        setBusy(false);
      }
    }

    async function runKnowledgeQuery() {
      const scopes = [];
      if ($('scope-private').checked) scopes.push('private');
      if ($('scope-public').checked) scopes.push('public');
      const payload = {
        query: $('query-text').value,
        mode: $('query-mode').value,
        scopes: scopes.length ? scopes : null,
        maxResults: 8,
      };
      setBusy(true);
      renderWorkbenchMessage('Running cited recall...');
      try {
        const data = await postJson('/api/knowledge/query', payload);
        renderQueryResult(data);
        renderRaw('Knowledge query', data);
        await refresh();
      } catch (error) {
        const payload = {ok: false, error: String(error.message || error)};
        renderWorkbenchMessage(payload.error);
        renderRaw('Knowledge query failed', payload);
      } finally {
        setBusy(false);
      }
    }

    async function inspectGraph() {
      const entity = encodeURIComponent($('graph-entity').value || '');
      setBusy(true);
      renderWorkbenchMessage('Inspecting cited graph evidence...');
      try {
        const data = await getJson(`/api/knowledge/graph/inspect?entity=${entity}&limit=20`);
        renderGraphResult(data);
        renderRaw('Graph inspect', data);
      } catch (error) {
        renderWorkbenchMessage(String(error.message || error));
      } finally {
        setBusy(false);
      }
    }

    async function inspectTimeline() {
      const entity = encodeURIComponent($('graph-entity').value || '');
      const atTime = encodeURIComponent($('graph-at-time').value || '');
      setBusy(true);
      renderWorkbenchMessage('Building temporal graph timeline...');
      try {
        const data = await getJson(`/api/knowledge/graph/timeline?entity=${entity}&atTime=${atTime}&limit=40`);
        renderTimelineResult(data);
        renderRaw('Graph timeline', data);
      } catch (error) {
        renderWorkbenchMessage(String(error.message || error));
      } finally {
        setBusy(false);
      }
    }

    async function runFreshness() {
      const entity = encodeURIComponent($('freshness-entity').value || '');
      const category = encodeURIComponent($('freshness-category').value || '');
      const atTime = encodeURIComponent($('freshness-at-time').value || '');
      setBusy(true);
      renderWorkbenchMessage('Checking freshness...');
      try {
        const data = await getJson(`/api/knowledge/freshness?entity=${entity}&category=${category}&atTime=${atTime}`);
        renderFreshnessResult(data);
        renderRaw('Freshness report', data);
      } catch (error) {
        renderWorkbenchMessage(String(error.message || error));
      } finally {
        setBusy(false);
      }
    }

    async function runSourceIngest() {
      const payload = {
        sourceType: $('source-type').value,
        title: $('source-title').value,
        occurredAt: $('source-occurred-at').value,
        scope: $('source-scope').value,
        participants: $('source-participants').value,
        text: $('source-text').value,
      };
      setBusy(true);
      renderWorkbenchMessage('Ingesting source...');
      try {
        const data = await postJson('/api/sources/ingest', payload);
        renderSourceResult(data);
        renderRaw('Source ingest', data);
        await refresh();
      } catch (error) {
        renderWorkbenchMessage(String(error.message || error));
      } finally {
        setBusy(false);
      }
    }

    async function showCompiledTruth() {
      switchWorkbench('truth');
      setBusy(true);
      renderWorkbenchMessage('Loading compiled truth...');
      try {
        const data = await getJson('/api/knowledge/truth');
        const text = data.text || JSON.stringify(data.projection || data, null, 2);
        $('workbench-output').innerHTML = `<div class="answer">${escapeHtml(text)}</div>`;
        renderRaw('Compiled truth', data);
        await refresh();
      } catch (error) {
        renderWorkbenchMessage(String(error.message || error));
      } finally {
        setBusy(false);
      }
    }

    async function runRemoteSync() {
      await runRemote('/api/remote/sync', 'Remote sync check');
    }

    async function runRemoteUpload() {
      await runRemote('/api/remote/upload', 'Remote upload selected');
    }

    async function runRemote(url, label) {
      setBusy(true);
      try {
        const data = await postJson(url, {providers: selectedProviderIds()});
        renderRaw(label, data);
        await refresh();
      } catch (error) {
        renderRaw(label, {ok: false, error: String(error.message || error)});
      } finally {
        setBusy(false);
      }
    }

    function selectedProviderIds() {
      return Array.from(document.querySelectorAll('input[name="remote-provider"]:checked')).map(input => input.value);
    }

    function switchWorkbench(mode) {
      for (const name of ['query', 'graph', 'freshness', 'source', 'truth']) {
        $(`${name}-pane`).classList.toggle('hidden', name !== mode);
        $(`tab-${name}`).setAttribute('aria-selected', name === mode ? 'true' : 'false');
      }
    }

    function renderQueryResult(data) {
      const citations = (data.citations || []).slice(0, 8);
      const evidence = (data.evidence || []).slice(0, 5);
      const citationRows = citations.length ? citations.map(citation => {
        const ref = citation.sourceRef || citation.source_ref || citation.ref || citation.id || JSON.stringify(citation).slice(0, 120);
        const detail = citation.evidenceHash || citation.evidence_hash || citation.hash || citation.scope || '';
        return `<li><code>${escapeHtml(ref)}</code><span>${escapeHtml(detail)}</span></li>`;
      }).join('') : '<li>No citations returned.</li>';
      const evidenceRows = evidence.map(item => `<li><code>${escapeHtml(item.source_ref || item.sourceRef || '')}</code><span>${escapeHtml((item.text || item.sanitized_text || '').slice(0, 240))}</span></li>`).join('');
      $('workbench-output').innerHTML = `
        <div class="data-row">
          <span><strong>${escapeHtml(data.status || 'PASS')}</strong><span class="detail">Confidence ${escapeHtml(((data.confidence || {}).level) || 'unknown')} (${escapeHtml(((data.confidence || {}).score) || 0)})</span></span>
          ${badge(data.status === 'REFUSED' ? 'Refused' : 'Cited', data.status === 'REFUSED' ? 'warn' : 'ok')}
        </div>
        <div class="answer">${escapeHtml(data.answer || '')}</div>
        <ul class="evidence-list">${citationRows}${evidenceRows}</ul>
      `;
    }

    function renderGraphResult(data) {
      const entities = (data.entities || []).slice(0, 8).map(entity => `<li><code>${escapeHtml(entity.name || entity.entity_id || '')}</code><span>${escapeHtml(entity.type || '')} ${escapeHtml(entity.scope || '')} ${escapeHtml(entity.evidence_hash || '')}</span></li>`).join('');
      const edges = (data.edges || []).slice(0, 8).map(edge => `<li><code>${escapeHtml(edge.relation || edge.edge_id || '')}</code><span>${escapeHtml(edge.source_ref || '')} ${escapeHtml(edge.evidence_hash || '')}</span></li>`).join('');
      $('workbench-output').innerHTML = `
        <div class="data-row"><span><strong>${escapeHtml(data.status || 'PASS')}</strong><span class="detail">${(data.entities || []).length} entity match(es), ${(data.edges || []).length} edge match(es)</span></span>${badge(data.ok ? 'Cited' : 'Attention', data.ok ? 'ok' : 'warn')}</div>
        <ul class="evidence-list">${entities || '<li>No matching entities.</li>'}${edges}</ul>
      `;
    }

    function renderTimelineResult(data) {
      const asOf = (data.asOf || []).slice(0, 8).map(item => `<li><code>${escapeHtml(item.timestamp || '')}</code><span>${escapeHtml((item.text || '').slice(0, 220))}</span></li>`).join('');
      const after = (data.afterAsOf || []).slice(0, 8).map(item => `<li><code>${escapeHtml(item.timestamp || '')}</code><span>${escapeHtml((item.text || '').slice(0, 220))}</span></li>`).join('');
      $('workbench-output').innerHTML = `
        <div class="data-row"><span><strong>${escapeHtml(data.status || 'PASS')}</strong><span class="detail">${(data.asOf || []).length} as-of item(s), ${(data.afterAsOf || []).length} later item(s)</span></span>${badge(data.ok ? 'Timeline' : 'Attention', data.ok ? 'ok' : 'warn')}</div>
        <h3>As Of</h3>
        <ul class="evidence-list">${asOf || '<li>No as-of evidence.</li>'}</ul>
        <h3>After</h3>
        <ul class="evidence-list">${after || '<li>No later evidence.</li>'}</ul>
      `;
    }

    function renderFreshnessResult(data) {
      const items = (data.items || []).slice(0, 12).map(item => `<li><code>${escapeHtml(item.freshness || '')}</code><span>${escapeHtml(item.category || '')} ${escapeHtml(item.subject || '')} ${escapeHtml((item.reasons || []).join(', '))}</span></li>`).join('');
      const counts = Object.entries(data.counts || {}).map(([key, value]) => `${key}: ${value}`).join(', ') || 'none';
      $('workbench-output').innerHTML = `
        <div class="data-row"><span><strong>${escapeHtml(data.status || 'PASS')}</strong><span class="detail">${escapeHtml(counts)}</span></span>${badge(data.ok ? 'Freshness' : 'Attention', data.ok ? 'ok' : 'warn')}</div>
        <ul class="evidence-list">${items || '<li>No freshness items.</li>'}</ul>
      `;
    }

    function renderSourceResult(data) {
      if (!data.ok) {
        renderWorkbenchMessage(data.error || data.status || 'Source ingest failed');
        return;
      }
      const event = data.event || {};
      $('workbench-output').innerHTML = `
        <div class="data-row"><span><strong>${escapeHtml(data.status || 'PASS')}</strong><span class="detail">${escapeHtml(data.sourceType || '')} ${escapeHtml(data.title || '')}</span></span>${badge('Ledger', 'ok')}</div>
        <ul class="evidence-list"><li><code>${escapeHtml(event.event_id || '')}</code><span>${escapeHtml(event.source || '')} ${escapeHtml(event.scope || '')}</span></li></ul>
      `;
    }

    function renderVaultResult(data) {
      if (!data.ok) {
        const detail = data.error || data.status || 'Vault export failed';
        const next = (data.nextSteps || []).join(' ');
        $('vault-output').innerHTML = dataRow('Vault export', data.status || 'Attention', `${detail}${next ? ' ' + next : ''}`, false, 'warn');
        return;
      }
      $('vault-output').innerHTML = [
        dataRow('Vault', 'Ready', data.path || 'Generated vault', true),
        dataRow('Manifest', `${data.fileCount || 0} files`, data.manifest || '', true),
        dataRow('Coverage', `${data.eventCount || 0} events`, `${data.documentCount || 0} documents, ${data.entityCount || 0} entities, ${data.edgeCount || 0} edges`, true),
        dataRow('Authority', 'Ledger', data.note || 'Total Recall remains authority.', true),
      ].join('');
    }

    function renderVaultImportResult(data) {
      if (!data.ok) {
        $('vault-output').innerHTML = dataRow('Vault import', data.status || 'Attention', data.error || 'Import failed', false, 'warn');
        return;
      }
      if (data.status === 'PREVIEW') {
        const proposals = (data.proposals || []).slice(0, 5).map(item => dataRow(item.proposal_id || 'proposal', item.note || 'note', item.title || '', true)).join('');
        $('vault-output').innerHTML = [
          dataRow('Import preview', data.preview_id || 'Ready', `${data.proposalCount || 0} proposal(s)`, true),
          proposals,
        ].join('');
        return;
      }
      $('vault-output').innerHTML = dataRow('Import promote', data.status || 'PASS', `${data.eventCount || 0} ledger event(s) written`, true);
    }

    function renderWorkbenchMessage(message) {
      $('workbench-output').innerHTML = `<div class="detail">${escapeHtml(message)}</div>`;
    }

    function gate(name, ok, detail) {
      const tone = ok ? 'ok' : 'warn';
      return `<div class="gate"><span><strong>${titleWithHelp(name)}</strong><span class="detail">${escapeHtml(detail || '')}</span></span>${badge(ok ? 'Pass' : 'Attention', tone)}</div>`;
    }

    function dataRow(name, value, detail, ok, forcedTone) {
      const tone = forcedTone || (ok ? 'ok' : value === 'planned' || value === 'NO_EVAL' ? 'warn' : 'neutral');
      return `<div class="data-row"><span><strong>${titleWithHelp(name)}</strong><span class="detail">${escapeHtml(detail || '')}</span></span>${badge(escapeHtml(value || '-'), tone)}</div>`;
    }

    function titleWithHelp(name) {
      const help = helpText(name);
      const label = escapeHtml(name);
      if (!help) return label;
      return `<span class="row-title">${label}<span class="help-mark" tabindex="0" role="button" title="${escapeHtml(help)}" data-help-title="${label}" data-help="${escapeHtml(help)}" aria-label="Help for ${label}">?</span></span>`;
    }

    function helpText(name) {
      const help = {
        'Memory Vault': 'The append-only Total Recall ledger. This is the authority for saved memory.',
        'Restore Point': 'The latest signed checkpoint and anchor. Saving a restore point protects the current ledger position, but does not rebuild derived search or quality views.',
        'Search Catalog': 'A rebuildable index/graph/compiled-truth layer used for fast cited answers. It can be stale even when the restore point is current.',
        'HF CLI': 'Whether the Hugging Face command-line tool is installed on this machine.',
        'HF login': 'Whether the local HF CLI has an authenticated session. Token values are never shown.',
        'HF dataset': 'The private Hugging Face dataset id used for encrypted clone transport.',
        'HF token visibility': 'The dashboard reports only the auth source, never the token string.',
        'Clone passphrase': 'Whether the local clone encryption passphrase is present in the environment. The passphrase value is never displayed.',
        'Latest encrypted clone': 'Most recent encrypted portable clone manifest found locally.',
        'Clone ledger point': 'The ledger event/hash captured by the encrypted clone manifest.',
        'Restore default': 'Restores should go to a test home first, then verify and Trust Gate before active replacement.',
        'Overall context risk': 'Plain-English summary of restart, compaction, and restore readiness.',
        'Current Context Risk Zone': 'Current safety zone for restart, compaction, and restore decisions.',
        'Checkpoint lag': 'How many memory events exist after the latest restore point. Non-zero means save first.',
        'Safe to restart?': 'Whether the current restore point and incident state are safe enough for process restart.',
        'Safe to compact?': 'Whether compaction has a current restore point and passing Trust Gate.',
        'Safe to restore?': 'Whether restore has passed the save, verify, trust, and backup checks.',
        'Profile: default': 'Default Hermes profile continuity status. Other profile rows stay isolated by profile.',
        'Agent Fleet': 'Read-only overview of Hermes profiles and their local continuity safety state.',
        'Protected action ladder': 'Ordered safety ladder before any future fresh hydrated session action.',
        'Rehydrate Preview': 'Read-only generated context preview. It does not start a new session or write memory.',
        'Restart readiness': 'Whether the latest restore point is current enough to restart safely.',
        'Compaction readiness': 'Whether a compact/restart handoff has a current restore point and passing Trust Gate.',
        'Restore readiness': 'Whether restored memory can be trusted after test restore, verify, and Trust Gate.',
        'Loop evidence': 'Read-only status from an agent/work loop. It is evidence, not execution authority.',
        'Active loops': 'Open agent/work loops awaiting evidence, review, verification, or completion.',
        'Total Recall store': 'Whether the local Total Recall home exists.',
        'Trust Gate': 'Hard-coded execution verification gate. Passing means required checks actually ran.',
        'Backups': 'Whether local backup archives exist. Backups are transport/recovery, not authority by themselves.',
        'Private HF dataset': 'Configured dataset id for encrypted clone transport. It should be private.',
        'Remote MCP': 'Remote control-plane serving is intentionally planned/guarded until auth and scopes are implemented.',
        'Index': 'Searchable source index rebuilt from the memory vault. If stale, click Rebuild Index or Fix All.',
        'Graph': 'Derived entities and relationships rebuilt from cited ledger evidence.',
        'Compiled Truth': 'Readable projection built from the Search Catalog. It is derived, not authority.',
        'Synthesis': 'Optional provisional learning/summary output. It does not silently rewrite memory.',
        'Release Gate': 'Quality scorecard for the Search Catalog and compiled truth. NO_EVAL means it has not been run for the current derived state.',
        'Quality Check': 'Short dashboard label for the Release Gate scorecard.',
        'Backup coverage': 'Whether the latest backup archive covers the same event count as the local memory vault.',
        'Restore guard': 'Whether verified restore/rehydrate is safe from the current local memory state.'
      };
      return help[name] || '';
    }

    function badge(text, tone) {
      return `<span class="badge ${tone || 'neutral'}">${text}</span>`;
    }

    function providerTone(status) {
      status = String(status || '');
      if (status.includes('available')) return 'ok';
      if (status.includes('planned')) return 'warn';
      return 'neutral';
    }

    function knowledgeSummary(knowledge) {
      const index = knowledge.index || {};
      const graph = knowledge.graph || {};
      if (!knowledge.ok) return knowledge.error || knowledge.status || 'needs attention';
      return `${index.sourceCount || 0} source(s), ${graph.entityCount || 0} graph node(s)`;
    }

    function renderRaw(title, payload) {
      $('console-title').textContent = title || 'Activity Console';
      $('raw-json').textContent = JSON.stringify(payload, null, 2);
    }

    function clearConsole() {
      $('console-title').textContent = 'Activity Console';
      $('raw-json').textContent = 'Ready.';
    }

    function setBusy(busy) {
      document.querySelectorAll('button').forEach(button => button.disabled = busy);
    }

    function formatBytes(bytes) {
      if (!bytes) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let value = Number(bytes) || 0;
      let unit = 0;
      while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
      }
      return value.toFixed(unit ? 1 : 0) + ' ' + units[unit];
    }

    async function copyText(value) {
      if (navigator.clipboard) await navigator.clipboard.writeText(value);
    }

    function titleCase(value) {
      value = String(value || '');
      return value ? value.charAt(0).toUpperCase() + value.slice(1) : '-';
    }

    function escapeHtml(value) {
      return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }

    function escapeJs(value) {
      return String(value == null ? '' : value).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n').replace(/\r/g, '');
    }

    function showSelectedHelp(mark) {
      if (!mark) return;
      const help = mark.getAttribute('data-help') || mark.getAttribute('title') || mark.getAttribute('aria-label') || '';
      if (!help) return;
      const title = mark.getAttribute('data-help-title') || ((mark.closest('.eyeline') || mark.closest('.row-title') || {}).textContent || 'Help').replace('?', '').trim() || 'Help';
      $('selected-help-title').textContent = title;
      $('selected-help-text').textContent = help;
    }

    document.addEventListener('click', event => {
      const mark = event.target.closest && event.target.closest('.help-mark');
      if (mark) showSelectedHelp(mark);
    });
    document.addEventListener('focusin', event => {
      const mark = event.target.closest && event.target.closest('.help-mark');
      if (mark) showSelectedHelp(mark);
    });
    document.addEventListener('keydown', event => {
      const mark = event.target.closest && event.target.closest('.help-mark');
      if (mark && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        showSelectedHelp(mark);
      }
    });

    refresh();
  </script>
</body>
</html>
"""
