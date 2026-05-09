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
total-recall backup run --out-dir ~/total-recall-backups --keep 14
```

Inspect existing backups:

```bash
total-recall backup status --out-dir ~/total-recall-backups
```

The backup cycle creates a timestamped bundle, runs `doctor`, runs `verify`, and
then deletes older `total-recall-backup-*.tar.gz` bundles beyond the configured
retention count.

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
- encrypted local removable-drive mirror

Remote receipts should never become continuity authority. The ledger,
checkpoints, anchors, and local verification remain the trust boundary.
