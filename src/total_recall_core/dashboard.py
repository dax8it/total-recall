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
                self._send_html(_HTML)
                return
            if parsed.path == "/api/status":
                self._send_json({
                    "ok": True,
                    "health": core.health(),
                    "index": core.index_status(),
                    "backup": core.backup_status(str(backup_dir)),
                    "summary": _summary(core),
                    "providers": _providers(),
                    "policy": {
                        "backupDir": str(backup_dir.expanduser()),
                        "keep": keep,
                        "keepDays": keep_days,
                        "home": str(core.home),
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
            self._send_json({"ok": False, "error": "not_found"}, status=404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/backup/run":
                self._send_json(core.backup_run(str(backup_dir), keep=keep, keep_days=keep_days))
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


_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Total Recall Dashboard</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7fa;
      --panel: #ffffff;
      --ink: #18202b;
      --muted: #667085;
      --line: #d9dee7;
      --soft: #eef2f6;
      --accent: #0f766e;
      --danger: #b42318;
      --ok: #027a48;
      --warn: #b54708;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--ink); }
    header { padding: 24px 28px 16px; border-bottom: 1px solid var(--line); background: var(--panel); }
    h1 { margin: 0 0 6px; font-size: 24px; line-height: 1.2; }
    main { padding: 24px 28px 32px; display: grid; gap: 16px; grid-template-columns: 1.05fr .95fr; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; }
    h2 { margin: 0 0 14px; font-size: 16px; }
    p { margin: 0 0 12px; color: var(--muted); font-size: 14px; line-height: 1.45; }
    button, a.button {
      appearance: none; border: 1px solid var(--line); background: #fff; color: var(--ink);
      padding: 9px 12px; border-radius: 6px; font-weight: 650; font-size: 14px;
      text-decoration: none; display: inline-flex; align-items: center; min-height: 36px; cursor: pointer;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button:disabled { opacity: .55; cursor: wait; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .metric { border: 1px solid var(--line); border-radius: 8px; padding: 12px; min-height: 92px; background: #fff; }
    .label { color: var(--muted); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0; }
    .value { margin-top: 8px; font-size: 20px; font-weight: 750; overflow-wrap: anywhere; }
    .sub { margin-top: 4px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .ok { color: var(--ok); }
    .bad { color: var(--danger); }
    .warn { color: var(--warn); }
    .chip { border-radius: 999px; padding: 3px 8px; font-size: 12px; font-weight: 750; background: var(--soft); }
    .chip.ok { background: #ecfdf3; }
    .chip.warn { background: #fffaeb; }
    .chip.bad { background: #fef3f2; }
    .gate-list { display: grid; gap: 8px; margin-top: 12px; }
    .gate { display: flex; justify-content: space-between; gap: 10px; border: 1px solid var(--line); border-radius: 8px; padding: 10px; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    pre { white-space: pre-wrap; overflow: auto; background: #101828; color: #e4e7ec; padding: 14px; border-radius: 8px; font-size: 12px; max-height: 420px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    td, th { border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; }
    .span { grid-column: 1 / -1; }
    .path { overflow-wrap: anywhere; color: var(--muted); font-size: 13px; }
    .hidden { display: none; }
    .providers { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .provider { border: 1px solid var(--line); border-radius: 8px; padding: 10px; cursor: pointer; }
    .provider-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
    .provider-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
    .provider input { width: 16px; height: 16px; flex: 0 0 auto; accent-color: var(--accent); }
    .remote-actions { margin-top: 12px; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; padding: 16px; }
      header { padding: 20px 16px 14px; }
      .grid, .providers { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Total Recall</h1>
    <div class="path" id="home">Loading local store...</div>
  </header>
  <main>
    <section class="span">
      <h2>Protection Status</h2>
      <div class="grid">
        <div class="metric"><div class="label">Overall</div><div class="value" id="overall">-</div><div class="sub" id="overall-sub"></div></div>
        <div class="metric"><div class="label">Checkpoint</div><div class="value" id="checkpoint">-</div><div class="sub" id="checkpoint-sub"></div></div>
        <div class="metric"><div class="label">Incidents</div><div class="value" id="incidents">-</div><div class="sub">open fail-closed records</div></div>
        <div class="metric"><div class="label">Latest Backup</div><div class="value" id="latest">-</div><div class="sub" id="retention"></div></div>
      </div>
      <div class="gate-list" id="gates"></div>
    </section>
    <section>
      <h2>Run Protection Cycle</h2>
      <p>Creates a fresh checkpoint, exports a private backup bundle, runs doctor, runs verify, and prunes old backups by retention policy.</p>
      <div class="row">
        <button class="primary" onclick="runBackup()">Backup + Doctor + Verify</button>
        <button onclick="runAction('/api/checkpoint')">Checkpoint Now</button>
        <button onclick="runAction('/api/doctor')">Doctor</button>
        <button onclick="runAction('/api/verify')">Verify</button>
        <button onclick="refresh()">Refresh</button>
      </div>
      <div class="gate-list" id="progress"></div>
      <button id="toggle-raw" class="hidden" onclick="toggleRaw()">Show raw output</button>
      <pre id="result" class="hidden">Ready.</pre>
    </section>
    <section>
      <h2>Automation</h2>
      <p>A plist is a macOS LaunchAgent file. Loading it schedules the same protection cycle daily, even if you forget.</p>
      <div class="row">
        <a class="button" href="/api/launchd.plist" target="_blank">View plist</a>
      </div>
      <pre id="command"></pre>
    </section>
    <section class="span">
      <h2>Backups</h2>
      <table>
        <thead><tr><th>File</th><th>Size</th><th>Modified</th><th>Actions</th></tr></thead>
        <tbody id="backups"><tr><td colspan="4">Loading...</td></tr></tbody>
      </table>
    </section>
    <section class="span">
      <h2>Remote Backup Providers</h2>
      <p>Choose one or more targets. Local folders and synced folders work now; direct cloud adapters will require encrypted bundles and Keychain-held credentials.</p>
      <div class="providers" id="providers"></div>
      <div class="row remote-actions">
        <button onclick="runRemoteSync()">Sync Check</button>
        <button class="primary" onclick="runRemoteUpload()">Upload Selected</button>
      </div>
      <div class="gate-list" id="remote-progress"></div>
      <button id="toggle-remote-raw" class="hidden" onclick="toggleRemoteRaw()">Show remote raw output</button>
      <pre id="remote-result" class="hidden">Ready.</pre>
    </section>
  </main>
  <script>
    let rawVisible = false;
    let remoteRawVisible = false;
    async function getJson(url, options) {
      const response = await fetch(url, options);
      return response.json();
    }
    async function refresh() {
      const data = await getJson('/api/status');
      document.getElementById('home').textContent = data.policy.home;
      renderSummary(data);
      renderAutomation(data);
      renderBackups(data.backup.backups);
      renderProviders(data.providers);
    }
    function renderSummary(data) {
      const summary = data.summary || {};
      const healthy = data.health.ok && summary.openIncidents === 0;
      const checkpointStatus = summary.checkpointStatus || 'missing';
      setMetric('overall', healthy ? 'Healthy' : 'Attention', healthy ? 'ok' : 'warn');
      document.getElementById('overall-sub').textContent = `${summary.eventCount || 0} ledger events`;
      setMetric('checkpoint', checkpointStatus === 'current' ? 'Current' : checkpointStatus === 'stale' ? 'Stale' : 'Missing', checkpointStatus === 'current' ? 'ok' : 'warn');
      document.getElementById('checkpoint-sub').textContent = checkpointStatus === 'stale' ? `${summary.checkpointLagEvents} event(s) since last checkpoint` : 'anchored checkpoint status';
      setMetric('incidents', String(summary.openIncidents || 0), summary.openIncidents ? 'warn' : 'ok');
      document.getElementById('latest').textContent = data.backup.latest ? data.backup.latest.split('/').pop() : 'None';
      document.getElementById('retention').textContent = retentionText(data.policy);
      document.getElementById('gates').innerHTML = [
        gate('Ledger hash chain', data.health.ok, data.health.ok ? 'Readable and hash-chained' : 'Needs inspection'),
        gate('Checkpoint freshness', checkpointStatus === 'current', checkpointStatus === 'current' ? 'Fresh checkpoint exists' : 'Run Checkpoint Now or protection cycle'),
        gate('Derived indexes', data.index.fresh, data.index.fresh ? 'Fresh derived indexes' : 'Will rebuild from ledger when needed'),
        gate('Backup inventory', data.backup.count > 0, data.backup.count ? `${data.backup.count} backup file(s)` : 'No backup file yet'),
      ].join('');
    }
    function renderAutomation(data) {
      const origin = window.location.origin;
      document.getElementById('command').textContent =
        'mkdir -p ~/Library/LaunchAgents\n' +
        'curl -s ' + origin + '/api/launchd.plist > ~/Library/LaunchAgents/com.total-recall.backup.plist\n' +
        'launchctl unload ~/Library/LaunchAgents/com.total-recall.backup.plist 2>/dev/null || true\n' +
        'launchctl load ~/Library/LaunchAgents/com.total-recall.backup.plist';
    }
    function renderBackups(backups) {
      const rows = backups.map(item => {
        const encoded = encodeURIComponent(item.path);
        return '<tr><td class="path">' + escapeHtml(item.path) + '</td><td>' + formatBytes(item.bytes) + '</td><td>' + item.modified + '</td><td class="row"><a class="button" href="/api/backups/download?path=' + encoded + '">Download</a><button data-copy-path="' + escapeHtml(item.path) + '" onclick="copyText(this.dataset.copyPath)">Copy path</button></td></tr>';
      });
      document.getElementById('backups').innerHTML = rows.length ? rows.join('') : '<tr><td colspan="4">No backups yet.</td></tr>';
    }
    function renderProviders(providers) {
      document.getElementById('providers').innerHTML = providers.map(provider => `
        <label class="provider">
          <div class="provider-header">
            <span class="provider-title">
              <input type="checkbox" name="remote-provider" value="${escapeHtml(provider.id)}" ${provider.default ? 'checked' : ''}>
              <strong>${escapeHtml(provider.name)}</strong>
            </span>
            <span class="chip ${providerClass(provider.status)}">${escapeHtml(provider.status)}</span>
          </div>
          <p>${escapeHtml(provider.note)}</p>
        </label>
      `).join('');
    }
    function selectedProviderIds() {
      return Array.from(document.querySelectorAll('input[name="remote-provider"]:checked')).map(input => input.value);
    }
    async function postJson(url, payload) {
      return getJson(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
    }
    async function runRemoteSync() {
      setBusy(true);
      document.getElementById('remote-progress').innerHTML = gate('Sync check', null, 'comparing local checkpoint with latest archive...');
      const data = await postJson('/api/remote/sync', {providers: selectedProviderIds()});
      renderRemoteResult(data);
      setBusy(false);
      await refresh();
    }
    async function runRemoteUpload() {
      setBusy(true);
      document.getElementById('remote-progress').innerHTML = gate('Upload selected', null, 'creating local backup and checking selected providers...');
      const data = await postJson('/api/remote/upload', {providers: selectedProviderIds()});
      renderRemoteResult(data);
      setBusy(false);
      await refresh();
    }
    function renderRemoteResult(data) {
      const steps = [];
      const sync = data.localSync || data.sync;
      if (sync) {
        steps.push(gate('Local vs latest archive', sync.relation === 'in_sync', sync.message || sync.relation));
      }
      for (const item of (data.results || [])) {
        const provider = item.provider || {};
        const detail = item.message || (item.sync ? item.sync.message : item.relation) || 'checked';
        steps.push(gate(provider.name || 'Provider', !!item.ok, detail));
      }
      document.getElementById('remote-progress').innerHTML = steps.length ? steps.join('') : gate('Remote sync', false, 'No provider selected.');
      document.getElementById('remote-result').textContent = JSON.stringify(data, null, 2);
      document.getElementById('toggle-remote-raw').classList.remove('hidden');
    }
    async function runAction(url) {
      setBusy(true);
      renderProgress(['Starting request']);
      const data = await getJson(url, {method: 'POST'});
      renderActionResult(data);
      setBusy(false);
      await refresh();
    }
    async function runBackup() {
      setBusy(true);
      renderProgress(['Checkpointing current ledger', 'Exporting backup bundle', 'Running doctor', 'Running verify', 'Applying retention']);
      const data = await getJson('/api/backup/run', {method: 'POST'});
      renderActionResult(data);
      setBusy(false);
      await refresh();
    }
    function renderActionResult(data) {
      const ok = !!data.ok;
      const steps = [];
      if (data.checkpoint) steps.push(gate('Checkpoint', data.checkpoint.ok, data.checkpoint.checkpointFile || 'checkpoint created'));
      if (data.backup) steps.push(gate('Export', data.backup.ok, data.backup.bundle || 'bundle created'));
      if (data.doctor) steps.push(gate('Doctor', data.doctor.ok, data.doctor.status || 'doctor complete'));
      if (data.verification) steps.push(gate('Verify', data.verification.ok, (data.verification.failures || data.verification.warnings || ['PASS']).join(', ')));
      if (data.retention) steps.push(gate('Retention', true, `${(data.retention.pruned || []).length} old backup(s) pruned`));
      if (!steps.length) steps.push(gate('Result', ok, data.status || (ok ? 'PASS' : 'Needs attention')));
      document.getElementById('progress').innerHTML = steps.join('');
      document.getElementById('result').textContent = JSON.stringify(data, null, 2);
      document.getElementById('toggle-raw').classList.remove('hidden');
    }
    function renderProgress(labels) {
      document.getElementById('progress').innerHTML = labels.map(label => gate(label, null, 'running...')).join('');
      document.getElementById('result').classList.add('hidden');
      rawVisible = false;
    }
    function gate(name, ok, detail) {
      const klass = ok === null ? 'warn' : ok ? 'ok' : 'bad';
      const label = ok === null ? 'Running' : ok ? 'Pass' : 'Attention';
      return '<div class="gate"><div><strong>' + escapeHtml(name) + '</strong><div class="sub">' + escapeHtml(detail || '') + '</div></div><span class="chip ' + klass + '">' + label + '</span></div>';
    }
    function toggleRaw() {
      rawVisible = !rawVisible;
      document.getElementById('result').classList.toggle('hidden', !rawVisible);
      document.getElementById('toggle-raw').textContent = rawVisible ? 'Hide raw output' : 'Show raw output';
    }
    function toggleRemoteRaw() {
      remoteRawVisible = !remoteRawVisible;
      document.getElementById('remote-result').classList.toggle('hidden', !remoteRawVisible);
      document.getElementById('toggle-remote-raw').textContent = remoteRawVisible ? 'Hide remote raw output' : 'Show remote raw output';
    }
    function setMetric(id, value, klass) {
      const el = document.getElementById(id);
      el.textContent = value;
      el.className = 'value ' + klass;
    }
    function retentionText(policy) {
      const count = policy.keep ? `latest ${policy.keep} files` : 'all files';
      return policy.keepDays === null || policy.keepDays === undefined ? count : `${count}, max age ${policy.keepDays} day(s)`;
    }
    function setBusy(busy) {
      document.querySelectorAll('button').forEach(button => button.disabled = busy);
    }
    function formatBytes(bytes) {
      if (!bytes) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB'];
      let value = bytes;
      let unit = 0;
      while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
      return value.toFixed(unit ? 1 : 0) + ' ' + units[unit];
    }
    function providerClass(status) {
      return status.includes('available') ? 'ok' : status.includes('planned') ? 'warn' : '';
    }
    async function copyText(value) {
      await navigator.clipboard.writeText(value);
    }
    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    refresh();
  </script>
</body>
</html>
"""
