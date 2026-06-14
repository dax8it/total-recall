# Install And Run Total Recall

This is the fast path from zero to a verified local memory store. Total Recall is
local-first and framework-agnostic: it runs as a standalone CLI/core, and Hermes
Agent can use it through an optional memory-provider plugin.

For the conceptual model, see [architecture.md](architecture.md). For day-to-day
operation, see [operational-manual.md](operational-manual.md).

## Requirements

- Python 3.10+
- A local working directory for the memory store
- Optional: LanceDB (vector-ish local index) via the `semantic` extra
- Optional: a `qmd` executable for the QMD retrieval layer

LanceDB and QMD are optional accelerators. Without them, Total Recall still works
using the SQLite/FTS index and a lexical fallback.

## Install

### From a published package

```bash
pip install total-recall-core
```

### From a checkout (development)

```bash
git clone https://github.com/dax8it/total-recall.git
cd total-recall
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[semantic]'   # the [semantic] extra adds LanceDB; omit to stay light
python -m pytest -q
```

You can also run directly from the checkout without installing:

```bash
PYTHONPATH=src python -m total_recall_core.cli health
./bin/total-recall health
```

## Choose where memory lives

Set `TOTAL_RECALL_HOME` to pick the local store directory. If unset, the core uses
a profile-appropriate default supplied by the caller. Under Hermes, the provider
uses `$HERMES_HOME/total-recall`.

```bash
export TOTAL_RECALL_HOME="$HOME/.total-recall"
```

Everything under `index/` in that directory is a derived retrieval cache that can
be rebuilt from the ledger; it is never the authority. See
[architecture.md](architecture.md) for the full directory layout.

## First five minutes

```bash
# 1. Add a memory event
total-recall ingest --kind note --session-id demo \
  --text "Decision: Total Recall verifies memory before rehydration."

# 2. Create a signed restore point
total-recall checkpoint --session-id demo --label first-checkpoint

# 3. Verify integrity (fails closed on tamper)
total-recall verify --session-id demo
#    expect: ok: true / status: PASS

# 4. Search and rehydrate (rehydrate verifies first)
total-recall search "verifies memory"
total-recall rehydrate --session-id demo --query "verification before rehydration"

# 5. Run the strict trust gate
total-recall trust verify --format text
```

`verify` checks the ledger hash chain, reduced state, checkpoint, and anchor.
`trust verify` is stricter: it also proves export/import persistence and runs
isolated source/freshness/timeline/vault/federation/plugin-bundle fixtures.

## Build local context

```bash
# Import files and folders into the ledger
total-recall documents ingest ./docs ./handoff.md --session-id demo

# Ingest working-context sources (meeting/email/Slack/GitHub/CRM/ticket/calendar)
total-recall sources ingest \
  --type meeting --title "Renewal Review" \
  --occurred-at 2026-01-05T12:00:00Z \
  --text "Decision: Renewal policy is month-to-month."

# Ask the Knowledge Engine for a cited answer
total-recall knowledge query --query "What did we decide about renewals?" --format text
```

## Open the operator dashboard

```bash
total-recall dashboard --backup-dir ~/total-recall-backups --keep 14 --keep-days 90
# open the printed local URL (default host is local-only)
```

The dashboard surfaces the Trust Spine, Knowledge Engine, Operator Workbench,
Obsidian vault export, and backups. See [backup-dashboard.md](backup-dashboard.md).

## Back up

```bash
total-recall backup run --out-dir ~/total-recall-backups --keep 14 --keep-days 90
total-recall backup status --out-dir ~/total-recall-backups
total-recall export --out total-recall-backup.tar.gz
total-recall import total-recall-backup.tar.gz
```

## Install for Hermes Agent

Total Recall plugs into Hermes Agent (by Nous Research) as an optional memory
provider. The plugin is a thin adapter over `total-recall-core`; all authoritative
continuity stays in the local ledger, checkpoints, and anchors.

```bash
total-recall hermes install --profile <profile> --activate --format text
hermes -p <profile> memory status
total-recall hermes doctor
```

From a checkout:

```bash
./scripts/install_hermes_plugin.sh --profile <profile> --activate --format text
```

To build a distributable plugin archive:

```bash
total-recall hermes bundle --out dist/total-recall-hermes-plugin.tar.gz
```

Full Hermes details, including compaction/rehydration policy and troubleshooting,
are in [hermes.md](hermes.md).

## Optional retrieval accelerators

```bash
# QMD: after `npm install -g @tobilu/qmd` or `bun install -g @tobilu/qmd`
total-recall qmd link
total-recall qmd link --bin-dir ~/.local/bin   # if no writable PATH dir exists

# Toggle backends
TOTAL_RECALL_ENABLE_LANCEDB=0
TOTAL_RECALL_ENABLE_QMD=0
TOTAL_RECALL_QMD_BIN=/path/to/qmd
```

## Verify your setup

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/benchmark_total_recall.py --events 250 --queries 25
```

For benchmark interpretation, see [benchmarks.md](benchmarks.md).
