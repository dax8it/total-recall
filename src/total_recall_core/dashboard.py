from __future__ import annotations

import json
import shlex
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

from .api import TotalRecallConfig, TotalRecallCore


def run_dashboard(
    *,
    home: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    backup_dir: Path,
    keep: int = 14,
) -> None:
    core = TotalRecallCore(TotalRecallConfig(home=home))
    server = ThreadingHTTPServer((host, port), _handler(core=core, backup_dir=backup_dir, keep=keep))
    print(f"Total Recall dashboard: http://{host}:{server.server_port}")
    server.serve_forever()


def _handler(*, core: TotalRecallCore, backup_dir: Path, keep: int) -> type[BaseHTTPRequestHandler]:
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
                    "policy": {
                        "backupDir": str(backup_dir.expanduser()),
                        "keep": keep,
                        "home": str(core.home),
                    },
                })
                return
            if parsed.path == "/api/launchd.plist":
                query = parse_qs(parsed.query)
                hour = int((query.get("hour") or ["3"])[0])
                minute = int((query.get("minute") or ["15"])[0])
                plist = _launchd_plist(home=core.home, backup_dir=backup_dir, keep=keep, hour=hour, minute=minute)
                self._send("application/xml", plist)
                return
            self._send_json({"ok": False, "error": "not_found"}, status=404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/backup/run":
                self._send_json(core.backup_run(str(backup_dir), keep=keep))
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


def _launchd_plist(*, home: Path, backup_dir: Path, keep: int, hour: int, minute: int) -> str:
    label = "com.total-recall.backup"
    command = (
        f"TOTAL_RECALL_HOME={shlex.quote(str(home))} total-recall backup run "
        f"--out-dir {shlex.quote(str(backup_dir.expanduser()))} --keep {keep}"
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
      --bg: #f7f8fb;
      --panel: #ffffff;
      --ink: #18202b;
      --muted: #697586;
      --line: #d9dee7;
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
    main { padding: 24px 28px 32px; display: grid; gap: 16px; grid-template-columns: 1.1fr .9fr; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 18px; }
    h2 { margin: 0 0 14px; font-size: 16px; }
    button, a.button {
      appearance: none; border: 1px solid var(--line); background: #fff; color: var(--ink);
      padding: 9px 12px; border-radius: 6px; font-weight: 650; font-size: 14px;
      text-decoration: none; display: inline-flex; align-items: center; min-height: 36px;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    button:disabled { opacity: .55; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
    .metric { border: 1px solid var(--line); border-radius: 8px; padding: 12px; min-height: 84px; }
    .label { color: var(--muted); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0; }
    .value { margin-top: 8px; font-size: 20px; font-weight: 750; overflow-wrap: anywhere; }
    .ok { color: var(--ok); }
    .bad { color: var(--danger); }
    .warn { color: var(--warn); }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    pre { white-space: pre-wrap; overflow: auto; background: #101828; color: #e4e7ec; padding: 14px; border-radius: 8px; font-size: 12px; max-height: 380px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    td, th { border-bottom: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; }
    .span { grid-column: 1 / -1; }
    .path { overflow-wrap: anywhere; color: var(--muted); font-size: 13px; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; padding: 16px; }
      header { padding: 20px 16px 14px; }
      .grid { grid-template-columns: 1fr; }
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
      <h2>Protection Policy</h2>
      <div class="grid">
        <div class="metric"><div class="label">Health</div><div class="value" id="health">-</div></div>
        <div class="metric"><div class="label">Backups Kept</div><div class="value" id="keep">-</div></div>
        <div class="metric"><div class="label">Latest Backup</div><div class="value" id="latest">-</div></div>
      </div>
    </section>
    <section>
      <h2>Run Checks</h2>
      <div class="row">
        <button class="primary" onclick="runBackup()">Export + Doctor + Verify</button>
        <button onclick="runAction('/api/doctor')">Doctor</button>
        <button onclick="runAction('/api/verify')">Verify</button>
        <button onclick="refresh()">Refresh</button>
      </div>
      <pre id="result">Ready.</pre>
    </section>
    <section>
      <h2>Automation</h2>
      <p class="path">Daily launchd plist template for this store and backup policy.</p>
      <div class="row">
        <a class="button" href="/api/launchd.plist" target="_blank">View plist</a>
      </div>
      <pre id="command"></pre>
    </section>
    <section class="span">
      <h2>Backups</h2>
      <table>
        <thead><tr><th>File</th><th>Size</th><th>Modified</th></tr></thead>
        <tbody id="backups"><tr><td colspan="3">Loading...</td></tr></tbody>
      </table>
    </section>
  </main>
  <script>
    async function getJson(url, options) {
      const response = await fetch(url, options);
      return response.json();
    }
    async function refresh() {
      const data = await getJson('/api/status');
      document.getElementById('home').textContent = data.policy.home;
      document.getElementById('health').textContent = data.health.ok ? 'OK' : 'Needs attention';
      document.getElementById('health').className = 'value ' + (data.health.ok ? 'ok' : 'bad');
      document.getElementById('keep').textContent = String(data.policy.keep);
      document.getElementById('latest').textContent = data.backup.latest ? data.backup.latest.split('/').pop() : 'None';
      const origin = window.location.origin;
      document.getElementById('command').textContent =
        'mkdir -p ~/Library/LaunchAgents\n' +
        'curl -s ' + origin + '/api/launchd.plist > ~/Library/LaunchAgents/com.total-recall.backup.plist\n' +
        'launchctl unload ~/Library/LaunchAgents/com.total-recall.backup.plist 2>/dev/null || true\n' +
        'launchctl load ~/Library/LaunchAgents/com.total-recall.backup.plist';
      const rows = data.backup.backups.map(item => (
        '<tr><td class="path">' + escapeHtml(item.path) + '</td><td>' + formatBytes(item.bytes) + '</td><td>' + item.modified + '</td></tr>'
      ));
      document.getElementById('backups').innerHTML = rows.length ? rows.join('') : '<tr><td colspan="3">No backups yet.</td></tr>';
    }
    async function runAction(url) {
      setBusy(true);
      const data = await getJson(url, {method: 'POST'});
      document.getElementById('result').textContent = JSON.stringify(data, null, 2);
      setBusy(false);
      await refresh();
    }
    async function runBackup() {
      await runAction('/api/backup/run');
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
    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    refresh();
  </script>
</body>
</html>
"""
