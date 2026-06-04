# Backup Dashboard

Total Recall includes a local admin control center for running and monitoring
the recommended protection cycle:

```text
export -> doctor -> verify -> retention pruning
```

The dashboard is portable. It uses Python's standard library HTTP server and
talks directly to `total-recall-core`; no external service is required. It also
surfaces the Trust Spine, Knowledge Engine status, query/graph/truth workbench,
Obsidian vault export, remote MCP readiness, remote backup providers, and backup
inventory from the same local-only control surface.

## Run The Dashboard

```bash
total-recall dashboard \
  --backup-dir ~/total-recall-backups \
  --keep 14 \
  --keep-days 90 \
  --host 127.0.0.1 \
  --port 8899
```

Open:

```text
http://127.0.0.1:8899
```

Use `--home` or `TOTAL_RECALL_HOME` to point the dashboard at a specific Hermes
profile store:

```bash
TOTAL_RECALL_HOME=/path/to/profile/total-recall \
  total-recall dashboard --backup-dir ~/total-recall-backups --keep 14
```

## CLI Automation

Run one backup cycle:

```bash
total-recall backup run --out-dir ~/total-recall-backups --keep 14 --keep-days 90
```

Inspect existing backups:

```bash
total-recall backup status --out-dir ~/total-recall-backups
```

Compare the current local store to the newest archive:

```bash
total-recall backup sync-status --out-dir ~/total-recall-backups
```

The backup cycle creates a fresh checkpoint, writes a timestamped bundle, runs
`doctor`, runs `verify`, and then deletes older
`total-recall-backup-*.tar.gz` bundles beyond the configured retention policy.

Retention options:

- `--keep 14` keeps the latest 14 backup files.
- `--keep-days 90` also deletes backups older than 90 days.
- omit `--keep-days` to keep by file count only.
- use a very large `--keep` and omit `--keep-days` for practical indefinite
  retention, limited by disk space.

## macOS Launchd

The dashboard exposes a launchd plist template at:

```text
http://127.0.0.1:8899/api/launchd.plist
```

Install it manually:

```bash
mkdir -p ~/Library/LaunchAgents
curl -s http://127.0.0.1:8899/api/launchd.plist \
  > ~/Library/LaunchAgents/com.total-recall.backup.plist
launchctl unload ~/Library/LaunchAgents/com.total-recall.backup.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.total-recall.backup.plist
```

The generated job runs daily and uses the dashboard's selected home, backup
directory, and retention count.

You can edit the plist before loading it to change the hour, minute, backup
directory, or retention flags.

## Admin Control Center

The first screen is the operator surface for a Hermes/Total Recall memory stack:

- status strip for authority, checkpoint, Knowledge Engine, scorecard, backups,
  and remote MCP readiness
- Trust Spine gates for ledger, checkpoint, incidents, retrieval, Knowledge
  Engine authority, and backup inventory
- Knowledge Engine controls for rebuild index, rebuild graph, build compiled
  truth, run scorecard, run synthesis, and show compiled truth
- Operator Workbench for cited queries, graph inspection, temporal timeline,
  freshness review, working-context source ingest, and compiled-truth review
- Obsidian Vault controls for choosing a local folder, generating wikilinked
  source/entity/document/timeline/decision/promise/task notes, previewing edited
  note imports, and promoting approved review proposals without using the CLI
- Remote MCP readiness rows that distinguish implemented local-provider
  behavior from planned OAuth, remote MCP HTTP serving, and live activity stream

The dashboard is still local by default. Remote MCP serving should remain
planned until OAuth/scoped clients and remote transport controls are wired.

## Private Remote Backups

Remote backups are feasible, but they should be encrypted before upload.

The dashboard has a provider selector and two remote controls:

- `Sync Check` compares the current local ledger state with the latest archive
  in the backup directory and reports `in_sync`, `local_ahead`,
  `archive_ahead`, or `diverged`.
- `Upload Selected` creates a fresh local backup and reports the selected
  provider status. Local folder and synced-folder providers work now; direct
  cloud adapters are intentionally blocked until encryption and credential
  storage are wired.

IPFS-style services such as Pinata are good for durable content-addressed
storage, but public IPFS content is not private by default. Even if a provider
offers private gateways or access controls, Total Recall bundles contain agent
memory and signing keys, so the safe pattern is:

```text
total-recall backup bundle
-> local encryption
-> remote upload
-> store receipt/CID as a non-authoritative backup receipt
```

Good future adapters:

- encrypted Pinata/IPFS uploader
- encrypted Arweave uploader
- encrypted S3-compatible uploader
- encrypted Google Drive uploader
- encrypted Dropbox uploader
- encrypted local removable-drive mirror

For travel or a second machine, the safest portable flow is:

```text
Machine A: backup run -> encrypt -> upload/sync
Machine B: download -> decrypt -> total-recall import
```

If the backup directory is inside iCloud Drive, Google Drive Desktop, Dropbox,
or another synced folder, Total Recall can already write local encrypted or
unencrypted bundles there. Direct API upload/download adapters should use OAuth
or provider API keys stored in the macOS Keychain, never in the repo or memory
ledger.

Remote receipts should never become continuity authority. The ledger,
checkpoints, anchors, and local verification remain the trust boundary.

See `docs/remote-backup-design.md` for the provider roadmap, encryption model,
approved-device model, and travel-machine restore flow.
