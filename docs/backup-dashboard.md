# Backup Dashboard

Total Recall includes a small local dashboard for running and monitoring the
recommended protection cycle:

```text
export -> doctor -> verify -> retention pruning
```

The dashboard is portable. It uses Python's standard library HTTP server and
talks directly to `total-recall-core`; no external service is required.

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

## Private Remote Backups

Remote backups are feasible, but they should be encrypted before upload.

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
