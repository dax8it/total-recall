# Hermes Agent Setup

This guide installs Total Recall as a Hermes Agent memory provider named
`total-recall`. The plugin is a thin adapter over `total-recall-core`; all
authoritative continuity remains in the local ledger, checkpoints, anchors, and
incidents written by the core.

## One-Command Install

Install the core package into the Python environment Hermes actually uses,
write the plugin bundle, enable it, select it for the profile, and verify it
with one command:

```bash
total-recall hermes install --profile <profile> --activate --format text
hermes -p <profile> memory status
```

The installer auto-detects Hermes' Python from the `hermes` wrapper, checks
whether `total_recall_core` imports there, and runs pip in that same
environment only when the package is missing or the version is stale. It then
writes a clean plugin bundle to:

```text
~/.hermes/plugins/total-recall
```

It validates the bundle and, when `--profile <profile> --activate` is present,
runs:

```bash
<hermes-python> -m pip install --upgrade <this-checkout-or-total-recall-core-version>
hermes plugins enable total-recall
hermes -p <profile> config set memory.provider total-recall
hermes -p <profile> memory status
```

Check the whole setup at any time:

```bash
total-recall hermes doctor
```

From a checkout of this repository, this single command installs the Python
package first and then writes/activates the Hermes plugin:

```bash
./scripts/install_hermes_plugin.sh --profile <profile> --activate --format text
```

## Install From Checkout Manually

Use this path only when debugging the installer or developing the plugin:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
total-recall health
total-recall hermes install --mode symlink --force --core-install always --format text
```

For Hermes installs that use a plugin directory different from
`~/.hermes/plugins`, pass `--plugin-dir /path/to/plugins`. For test or
nonstandard homes, pass `--hermes-home /path/to/hermes-home`. If auto-detection
cannot find Hermes' Python, pass `--hermes-python /path/to/hermes/venv/bin/python`.
For offline installs, pass `--core-source /path/to/total_recall_core.whl` or a
checkout path.

## Distributable Plugin Bundle

Build a clean archive for users, release assets, or plugin registry ingestion:

```bash
total-recall hermes bundle --out dist/total-recall-hermes-plugin.tar.gz
```

The archive contains:

```text
total-recall/
  __init__.py
  plugin.yaml
  README.md
```

The bundle is intentionally small. The provider implementation lives in the
installable `total_recall_core.hermes_provider` module, so plugin updates ship
through the Python package while Hermes sees a standard memory-provider bundle.

## Add Document Context

Use the built-in document flow when you want Hermes to remember a folder of
handoffs, policies, notes, or markdown docs without running a separate brain
system:

```bash
total-recall documents ingest ./docs ./handoff.md
total-recall knowledge query --query "What context did we import?" --format text
total-recall checkpoint --session-id documents --label document-import
```

The document flow writes normal ledger events, so imported context participates
in search, Knowledge Engine queries, backup/export, checkpoints, and verified
rehydration. See [document ingest](document-ingest.md) for supported formats,
limits, and dry-run examples.

Use source ingest for working-context events like meetings, email, Slack,
GitHub, CRM, tickets, calendars, and agent transcripts:

```bash
total-recall sources ingest \
  --type meeting \
  --title "Renewal Review" \
  --occurred-at 2026-01-05T12:00:00Z \
  --text "Decision: Renewal policy is month-to-month."
```

Hermes can also call the `total_recall_source_ingest` tool directly when it has
source text worth preserving.

## Export An Obsidian Vault

When you want a human-readable graph over Hermes continuity, export a local
Obsidian-compatible vault:

```bash
total-recall vault export --out ~/TotalRecallVault
```

The vault includes `Sources/`, `Entities/`, `Documents/`, `Timeline/`,
`Decisions/`, `Promises/`, and `Tasks/` notes with Obsidian wikilinks. It is a
derived projection: Total Recall's ledger, checkpoints, and anchors remain the
authority. Edited vault notes become memory only through
`total-recall vault import-preview` and `total-recall vault import-promote`.
See [Obsidian vault export](obsidian-vault-export.md).

## Smoke Profile

Test on a non-live profile before switching an active agent:

```bash
hermes profile create total-recall-smoke || true
hermes plugins enable total-recall
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
total-recall learning review --session-id smoke --no-persist --format text
total-recall rehydrate --session-id smoke --query "Hermes smoke memory"
total-recall doctor
```

Only switch a live profile after health, search, checkpoint, verify, rehydrate,
doctor, and the Hermes memory status check pass.

## Select Provider

```bash
total-recall hermes install --profile <profile> --activate
```

Manual equivalent:

```bash
hermes plugins enable total-recall
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
- `total_recall_trust_verify`
- `total_recall_learning_review`
- `total_recall_rehydrate`
- `total_recall_incidents`
- `total_recall_source_ingest`
- `total_recall_knowledge_query`
- `total_recall_knowledge_freshness`
- `total_recall_knowledge_status`
- `total_recall_knowledge_synthesis_status`
- `total_recall_knowledge_compiled_truth`
- `total_recall_knowledge_graph_inspect`
- `total_recall_knowledge_graph_timeline`
- `total_recall_federation_query`

`total_recall_learning_review` produces the overnight candidate-card preview. It
returns layer routing, action boundaries, promotion decisions, and a wake-up diff
without mutating the ledger.

`total_recall_federation_query` requires explicit `authorize=true` and returns
workspace-separated results. It does not silently merge another agent's memory
into the current profile.

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
total-recall trust verify
```

Common states:

- `anchor_not_found`: create a new checkpoint only after confirming the ledger
  and checkpoint history are expected.
- `anchor_signature_mismatch`: treat the store as tampered until investigated.
- `ledger_or_state_invalid`: do not rehydrate; inspect the ledger and restore
  from a known-good export if needed.
- `index_not_found` or stale index: run `total-recall index rebuild`; indexes
  are derived caches and can be rebuilt from the ledger.
