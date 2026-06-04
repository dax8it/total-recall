from __future__ import annotations

import json
import shlex
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
    class TotalRecallDashboardHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(_CONTROL_CENTER_HTML)
                return
            if parsed.path == "/api/status":
                self._send_json({
                    "ok": True,
                    "health": core.health(),
                    "index": core.index_status(),
                    "backup": core.backup_status(str(backup_dir)),
                    "summary": _summary(core),
                    "trustGate": _trust_gate_summary(core),
                    "knowledge": _knowledge_summary(core),
                    "mcp": _mcp_summary(core),
                    "providers": _providers(),
                    "policy": {
                        "backupDir": str(backup_dir.expanduser()),
                        "keep": keep,
                        "keepDays": keep_days,
                        "home": str(core.home),
                        "defaultVaultDir": str(Path.home() / "TotalRecallVault"),
                    },
                })
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


def _guarded_call(fn: Any) -> Dict[str, Any]:
    try:
        payload = fn()
        if isinstance(payload, dict):
            return payload
        return {"ok": True, "result": payload}
    except Exception as exc:
        return {"ok": False, "status": "ERROR", "error": str(exc)}


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
            {"name": "Hermes MemoryProvider", "status": "implemented", "detail": "Lifecycle hooks, rehydrate, verify, and Knowledge Engine tools."},
            {"name": "Remote MCP HTTP", "status": "planned", "detail": "This dashboard is the admin shape; secured remote serving still needs OAuth and scope enforcement."},
            {"name": "OAuth 2.1 clients", "status": "planned", "detail": "No remote clients are accepted by this local dashboard."},
            {"name": "Live activity stream", "status": "planned", "detail": "Current control center uses explicit JSON actions and refresh."},
            {"name": "Provider adapters", "status": "guarded", "detail": "External providers require explicit authorization and redacted payloads."},
        ],
    }


def _safe_backup_path(path: Path, backup_dir: Path) -> bool:
    try:
        resolved = path.resolve()
        root = backup_dir.expanduser().resolve()
        resolved.relative_to(root)
    except Exception:
        return False
    return resolved.is_file() and resolved.name.startswith("total-recall-backup-") and resolved.name.endswith(".tar.gz")


def _providers() -> list[Dict[str, Any]]:
    return [
        {"id": "local_folder", "name": "Local folder", "status": "available", "default": True, "note": "Works now. Point backup-dir at any local or synced folder."},
        {"id": "icloud_drive", "name": "iCloud Drive", "status": "available via folder", "default": True, "note": "Works now if backup-dir is inside your iCloud Drive folder."},
        {"id": "google_drive", "name": "Google Drive", "status": "planned", "default": True, "note": "First direct cloud adapter candidate: OAuth + resumable upload + encrypted bundles."},
        {"id": "arweave", "name": "Arweave", "status": "planned encrypted", "default": True, "note": "Durable archive layer: encrypted bundles, permanent receipts, manual approval for upload costs."},
        {"id": "github", "name": "GitHub", "status": "planned encrypted", "default": False, "note": "Good metadata/receipt mirror or private release assets; not primary memory authority."},
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
  <title>Total Recall Remote MCP Admin</title>
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
    .panel.vault, .panel.backups, .panel.providers { grid-column: 1 / -1; }
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
    .field select, .field input[type="text"] {
      border: 0;
      outline: 0;
      color: var(--ink);
      background: transparent;
      font-size: 13px;
      min-width: 88px;
    }
    .field.grow { flex: 1 1 340px; justify-content: flex-start; }
    .field.grow input[type="text"] { width: 100%; min-width: 220px; }
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
      .status-cell { border-bottom: 1px solid var(--line); }
      .panel.trust, .panel.knowledge, .panel.workbench, .panel.remote { grid-column: 1 / -1; }
    }
    @media (max-width: 760px) {
      .topbar { position: relative; grid-template-columns: 1fr; padding: 16px; }
      .toolbar { justify-content: flex-start; }
      .status-strip { margin: 14px 16px 0; grid-template-columns: 1fr; }
      .status-cell { border-right: 0; }
      .workspace { padding: 14px 16px 22px; grid-template-columns: 1fr; }
      .nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .action-grid { grid-template-columns: 1fr; }
      .gate, .data-row, .provider-row { grid-template-columns: 1fr; }
      .split-actions { justify-content: flex-start; }
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
        <span><strong>Total Recall</strong><span>Remote MCP Admin</span></span>
      </a>
      <nav class="nav" aria-label="Control center">
        <a class="active" href="#top"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 13h7V4H4v9zM13 20h7V4h-7v16zM4 20h7v-5H4v5z"/></svg>Overview</a>
        <a href="#trust"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3l7 3v5c0 4.5-2.8 8.5-7 10-4.2-1.5-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-5"/></svg>Trust Spine</a>
        <a href="#knowledge"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 6h16M4 12h16M4 18h10"/><path d="M17 15l3 3-3 3"/></svg>Knowledge</a>
        <a href="#remote"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M5 12a7 7 0 0 1 14 0"/><path d="M12 12v8"/><path d="M8 16l4 4 4-4"/></svg>Remote</a>
        <a href="#workbench"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M4 5h16v14H4z"/><path d="M8 9h8M8 13h5"/></svg>Workbench</a>
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
          <h1>Remote MCP Admin Control Center</h1>
          <div class="path" id="home-path">Loading Total Recall home...</div>
        </div>
        <div class="toolbar">
          <button onclick="refresh()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>Refresh</button>
          <button onclick="runAction('/api/checkpoint', 'Manual checkpoint')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>Checkpoint</button>
          <button class="primary" onclick="runProtectionCycle()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 3l7 3v5c0 4.5-2.8 8.5-7 10-4.2-1.5-7-5.5-7-10V6l7-3z"/></svg>Protection Cycle</button>
        </div>
      </header>

      <section class="status-strip" aria-label="System status">
        <div class="status-cell"><div class="eyeline">Authority</div><div class="status-value" id="metric-authority">-</div><div class="status-sub" id="metric-authority-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Checkpoint</div><div class="status-value" id="metric-checkpoint">-</div><div class="status-sub" id="metric-checkpoint-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Knowledge</div><div class="status-value" id="metric-knowledge">-</div><div class="status-sub" id="metric-knowledge-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Scorecard</div><div class="status-value" id="metric-score">-</div><div class="status-sub" id="metric-score-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Backups</div><div class="status-value" id="metric-backup">-</div><div class="status-sub" id="metric-backup-sub">-</div></div>
        <div class="status-cell"><div class="eyeline">Remote MCP</div><div class="status-value" id="metric-remote">-</div><div class="status-sub" id="metric-remote-sub">-</div></div>
      </section>

      <main class="workspace">
        <section class="panel trust" id="trust">
          <div class="panel-header">
            <div>
              <h2>Trust Spine</h2>
              <p>Ledger, checkpoint, anchor, incident, and retrieval gates.</p>
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
              <h2>Knowledge Engine</h2>
              <p>Cited recall, graph evidence, compiled truth, and scorecards.</p>
            </div>
          </div>
          <div class="panel-body">
            <div class="row-list" id="knowledge-rows"></div>
            <div class="action-grid" style="margin-top: 10px;">
              <button onclick="runKnowledgeAction('/api/knowledge/index/rebuild', 'Knowledge index rebuild')">Rebuild Index</button>
              <button onclick="runKnowledgeAction('/api/knowledge/graph/rebuild', 'Graph rebuild')">Rebuild Graph</button>
              <button onclick="runKnowledgeAction('/api/knowledge/truth/build', 'Compiled truth build')">Build Truth</button>
              <button onclick="runKnowledgeAction('/api/knowledge/evaluate/run', 'Evaluation scorecard')">Run Scorecard</button>
              <button onclick="runKnowledgeAction('/api/knowledge/synthesize/run', 'Derived synthesis')">Run Synthesis</button>
              <button onclick="showCompiledTruth()">Show Truth</button>
            </div>
          </div>
        </section>

        <section class="panel workbench" id="workbench">
          <div class="panel-header">
            <div>
              <h2>Operator Workbench</h2>
              <p>Recall, inspect, and verify the promise the agent is allowed to keep.</p>
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
              <h2>Remote MCP Readiness</h2>
              <p>Local authority now, guarded remote surface next.</p>
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
              <h2>Backups</h2>
              <p>Private archives, retention policy, and download handles.</p>
            </div>
            <a class="button" href="/api/launchd.plist" target="_blank">View LaunchAgent</a>
          </div>
          <div class="panel-body">
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
      renderProviders(data.providers || []);
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
      const checkpointStatus = summary.checkpointStatus || 'missing';
      const authorityOk = !!health.ok && !summary.openIncidents;
      const knowledgeOk = !!knowledge.ok;
      const scorecard = ((knowledge.scorecard || {}).scorecard) || {};
      const score = typeof scorecard.score === 'number' ? `${scorecard.score}/10` : 'No eval';

      $('home-path').textContent = (data.policy || {}).home || 'Unknown home';
      $('side-home').textContent = (data.policy || {}).home || 'Unknown home';
      $('side-state').textContent = authorityOk ? 'Verified local authority' : 'Attention required';
      $('side-dot').className = 'status-dot ' + (authorityOk ? 'ok' : 'bad');

      setMetric('metric-authority', authorityOk ? 'Verified' : 'Attention', authorityOk ? 'ok' : 'bad', `${summary.eventCount || 0} ledger event(s)`);
      setMetric('metric-checkpoint', titleCase(checkpointStatus), checkpointStatus === 'current' ? 'ok' : 'warn', checkpointStatus === 'stale' ? `${summary.checkpointLagEvents || 0} event(s) behind` : 'checkpoint freshness');
      setMetric('metric-knowledge', knowledgeOk ? 'Ready' : 'Degraded', knowledgeOk ? 'ok' : 'warn', knowledgeSummary(knowledge));
      setMetric('metric-score', score, scorecard.ok ? 'ok' : 'warn', scorecard.status || ((knowledge.scorecard || {}).status || 'evaluation status'));
      setMetric('metric-backup', backup.count ? String(backup.count) : 'None', backup.count ? 'ok' : 'warn', backup.latest ? backup.latest.split('/').pop() : 'no bundle yet');
      setMetric('metric-remote', 'Local', 'warn', 'remote OAuth MCP is planned');
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
      const checkpointStatus = summary.checkpointStatus || 'missing';
      const rows = [
        gate('Ledger hash chain', !!health.ok, health.ok ? 'Readable and chained' : 'Verify before trusted rehydrate'),
        gate('Checkpoint freshness', checkpointStatus === 'current', checkpointStatus === 'current' ? 'No ledger lag' : `${summary.checkpointLagEvents || 0} event(s) since latest checkpoint`),
        gate('Execution trust gate', trustGate.ok === true, trustGateSummary(trustGate)),
        gate('Incident posture', !summary.openIncidents, summary.openIncidents ? `${summary.openIncidents} open fail-closed incident(s)` : 'No open incidents'),
        gate('Core retrieval index', !!index.fresh, index.fresh ? 'Derived index is fresh' : 'Will rebuild from ledger authority'),
        gate('Knowledge authority', !!knowledge.ok, knowledge.ok ? 'Derived knowledge is gated by citations' : knowledge.error || knowledge.status || 'Knowledge layer needs attention'),
        gate('Backup inventory', !!backup.count, backup.count ? `${backup.count} archive(s) available` : 'Run protection cycle to create first archive'),
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
      return runAction('/api/backup/run', 'Protection cycle');
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
      return `<div class="gate"><span><strong>${escapeHtml(name)}</strong><span class="detail">${escapeHtml(detail || '')}</span></span>${badge(ok ? 'Pass' : 'Attention', tone)}</div>`;
    }

    function dataRow(name, value, detail, ok, forcedTone) {
      const tone = forcedTone || (ok ? 'ok' : value === 'planned' || value === 'NO_EVAL' ? 'warn' : 'neutral');
      return `<div class="data-row"><span><strong>${escapeHtml(name)}</strong><span class="detail">${escapeHtml(detail || '')}</span></span>${badge(escapeHtml(value || '-'), tone)}</div>`;
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

    refresh();
  </script>
</body>
</html>
"""
