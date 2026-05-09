# Hermes Agent Setup

This guide installs Total Recall as a Hermes Agent memory provider named
`total-recall`. The plugin is a thin adapter over `total-recall-core`; all
authoritative continuity remains in the local ledger, checkpoints, anchors, and
incidents written by the core.

## Install

From a checkout of this repository:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
total-recall health
```

Expose the plugin to Hermes under the profile plugin directory:

```bash
mkdir -p "$HERMES_HOME/plugins/memory"
ln -s "$PWD/hermes-plugin/total-recall" "$HERMES_HOME/plugins/memory/total-recall"
```

For Hermes installs that use a global plugin directory instead of a
profile-scoped plugin directory, place the same symlink in the documented
Hermes plugin path for that install.

## Smoke Profile

Test on a non-live profile before switching an active agent:

```bash
hermes profile create total-recall-smoke || true
hermes -p total-recall-smoke config set memory.provider total-recall
hermes -p total-recall-smoke memory status
```

Then run a core cycle against the same store:

```bash
total-recall health
total-recall ingest --kind note --text "Total Recall Hermes smoke memory." --session-id smoke
total-recall search "Hermes smoke memory"
total-recall checkpoint --session-id smoke
total-recall verify --session-id smoke
total-recall rehydrate --session-id smoke --query "Hermes smoke memory"
total-recall doctor
```

Only switch a live profile after health, search, checkpoint, verify, rehydrate,
doctor, and the Hermes memory status check pass.

## Select Provider

```bash
hermes -p <profile> config set memory.provider total-recall
hermes -p <profile> memory status
```

If you have agent aliases, run the equivalent config command through each alias
after confirming the alias targets the intended Hermes profile.

## Provider Hooks

The plugin implements the Hermes memory-provider lifecycle:

- `prefetch`
- `queue_prefetch`
- `sync_turn`
- `on_session_end`
- `on_session_switch`
- `on_pre_compress`
- `get_tool_schemas`
- `handle_tool_call`

Provider tools:

- `total_recall_search`
- `total_recall_status`
- `total_recall_checkpoint`
- `total_recall_verify`
- `total_recall_rehydrate`
- `total_recall_incidents`

## Automatic Rehydration

Hermes owns compaction thresholds. Total Recall adds provider policy that can
inject a verified rehydrate block when continuity risk rises.

Default triggers include startup or gateway restart, `/new`, `/resume`, session
id changes, compaction, repeated compactions, context usage crossing the
configured threshold, stale checkpoint detection, and low local continuity
confidence during prefetch.

Automatic rehydration is still verified. If verification fails, the plugin
returns a fail-closed warning instead of prior-memory content.

## Backup And Recovery

Create a portable backup:

```bash
total-recall export --out total-recall-backup.tar.gz
```

The export includes authoritative ledger, state, checkpoints, anchors, reports,
incidents, external-memory queues, and local signing keys. Treat the bundle as
private agent memory.

Restore into an empty store:

```bash
TOTAL_RECALL_HOME=/path/to/new/store total-recall import total-recall-backup.tar.gz
TOTAL_RECALL_HOME=/path/to/new/store total-recall verify
TOTAL_RECALL_HOME=/path/to/new/store total-recall doctor
```

Use `--replace` only when intentionally replacing the target store:

```bash
TOTAL_RECALL_HOME=/path/to/store total-recall import total-recall-backup.tar.gz --replace
```

`import` verifies the bundle manifest and rejects unsafe tar paths before
copying files. `verify` rebuilds derived indexes from the ledger after
authoritative checks pass.

For a managed local dashboard and retention policy:

```bash
TOTAL_RECALL_HOME=/path/to/profile/total-recall \
  total-recall dashboard --backup-dir ~/total-recall-backups --keep 14 --keep-days 90
```

See `docs/backup-dashboard.md` for launchd automation and private remote-backup
notes.

## Troubleshooting

Run:

```bash
total-recall doctor
hermes -p <profile> memory status
total-recall verify
```

Common states:

- `anchor_not_found`: create a new checkpoint only after confirming the ledger
  and checkpoint history are expected.
- `anchor_signature_mismatch`: treat the store as tampered until investigated.
- `ledger_or_state_invalid`: do not rehydrate; inspect the ledger and restore
  from a known-good export if needed.
- `index_not_found` or stale index: run `total-recall index rebuild`; indexes
  are derived caches and can be rebuilt from the ledger.
