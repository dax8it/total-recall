# Total Recall Operational Manual

This manual is the practical guide for running Total Recall as a local-first memory continuity layer for agents and operators.

Total Recall is not just a vector database, notebook, or chat history search tool. It is a continuity authority: it records memory as append-only evidence, signs checkpoints, verifies integrity before rehydration, and exposes cited recall through CLI, Hermes tools, and the local dashboard.

## What You Can Do With It

| Job | Command or surface | What you get |
|---|---|---|
| Install as Hermes memory | `total-recall hermes install --profile <profile> --activate` | Hermes uses Total Recall as the MemoryProvider |
| See if memory is safe to trust | `total-recall verify` / `total-recall trust verify` | fail-closed integrity verdicts with reports |
| Rehydrate an agent after restart/compaction | `total-recall rehydrate --session-id ... --query ...` | cited context only after verification passes |
| Search local memory | `total-recall search "query"` | local cited results from ledger-derived indexes |
| Build company/project context | `total-recall documents ingest ./docs` | local files become ledger-backed memory chunks |
| Ingest working context | `total-recall sources ingest --type meeting ...` | meetings/email/Slack/GitHub/CRM/tickets/calendar evidence |
| Ask the Knowledge Engine | `total-recall knowledge query --query ...` | cited answer with freshness and graph support |
| Check stale promises/decisions | `total-recall knowledge freshness ...` | current/stale/superseded classification |
| Ask “what did we know then?” | `total-recall knowledge graph timeline --at-time ...` | as-of evidence split from later changes |
| Export a reading vault | `total-recall vault export --out ~/TotalRecallVault` | Obsidian-compatible derived notes |
| Review edited notes before memory writes | `vault import-preview` then `vault import-promote` | explicit owner-controlled promotion |
| Operate visually | `total-recall dashboard` | Trust Spine, Knowledge, Workbench, Vault, Backups |
| Back up safely | `total-recall backup run --out-dir ...` | portable archive with checkpoint/export metadata |
| Federate agents/workspaces | `federation query --authorize` | workspace-separated cited results, no silent merge |

## Mental Model

Total Recall has one authority and several projections.

Authority:

```text
ledger/events.jsonl -> state/current.json -> checkpoints/*.json -> anchors/*.json
```

Derived projections:

```text
index/                 local search caches
knowledge/             graph, compiled truth, synthesis, eval, provider reports
reports/               audit artifacts and trust gate reports
reviews/obsidian/      edited-note preview artifacts
external-memory/       quarantine/promote/reject staging
```

Rules:

1. The ledger is the source of truth.
2. Checkpoints pin reduced state, event count, and last event hash.
3. Anchors sign checkpoint hashes.
4. Verify fails closed on tamper, missing anchors, or checkpoint mismatch.
5. Rehydrate verifies before assembling context.
6. Derived indexes can be rebuilt; they are not continuity authority.
7. Generated reports are audit artifacts, not memory sources.
8. Federation and external providers require explicit authorization.

## First 10 Minutes

### 1. Install

```bash
pip install total-recall-core
```

From a checkout:

```bash
git clone https://github.com/dax8it/total-recall.git
cd total-recall
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[semantic]'
```

The `semantic` extra is optional. Without it, SQLite/FTS and lexical fallback still work.

### 2. Create a memory store

```bash
export TOTAL_RECALL_HOME=$(mktemp -d)
total-recall ingest --kind note --session-id demo --text "Decision: Total Recall verifies memory before rehydration."
total-recall checkpoint --session-id demo --label first-demo-checkpoint
total-recall verify --session-id demo
```

Expected shape:

```text
ok: true
status: PASS
```

### 3. Query and rehydrate

```bash
total-recall search "verifies memory"
total-recall rehydrate --session-id demo --query "verification before rehydration"
```

The rehydrate path returns cited context only after verification passes.

### 4. Run the trust gate

```bash
total-recall trust verify --format text
```

The trust gate checks real-store authority and isolated synthetic fixtures: source ingest, freshness, temporal timeline, Obsidian import preview/promote, federation auth, export/import, and plugin bundle surface.

### 5. Open the dashboard

```bash
total-recall dashboard --backup-dir ~/total-recall-backups
```

Open the printed URL. The default host is local-only.

## Dashboard Tour

The dashboard is the operator console.

### Trust Spine

Shows whether the store is currently safe to trust:

- Ledger hash chain
- Checkpoint freshness
- Execution trust gate
- Incident posture
- Core retrieval index
- Knowledge authority
- Backup inventory

Buttons:

- `Doctor` runs a broader health check.
- `Verify` checks ledger/checkpoint/anchor integrity.
- `Trust Gate` runs the hard-coded release/day-one execution gate.

### Knowledge Engine

Shows source/index/graph/truth/synthesis/eval health.

Buttons:

- `Rebuild Index`
- `Rebuild Graph`
- `Build Truth`
- `Run Scorecard`
- `Run Synthesis`
- `Show Truth`

### Operator Workbench

Interactive local workflows:

- Query: cited recall answer
- Graph: entity/source evidence inspection
- Freshness: current/stale/superseded checks
- Source: ingest meeting/email/Slack/GitHub/CRM/ticket/calendar text
- Truth: compiled truth projection

### Obsidian Vault Export

Exports a derived reading vault and lets you preview/promote edited notes. Promotion writes explicit ledger events; edits do not silently become memory.

### Remote Backup Providers

Shows local folder / synced folder paths that work today and planned direct provider surfaces. The local dashboard is not an OAuth-secured remote MCP server yet.

## Core Workflows

### Add a note

```bash
total-recall ingest \
  --kind note \
  --session-id project-alpha \
  --scope private \
  --text "Decision: Project Alpha will keep public docs separate from private handoffs."
```

### Ingest local documents

```bash
total-recall documents ingest ./docs ./README.md --session-id project-alpha
```

Unsupported files, binary-looking files, oversized files, and excluded paths are skipped. Text files are chunked and written as hash-chained ledger events.

### Ingest a meeting or Slack thread

```bash
total-recall sources ingest \
  --type meeting \
  --title "Project Alpha Launch Review" \
  --actor "operator" \
  --participant "design" \
  --participant "engineering" \
  --occurred-at 2026-01-05T15:00:00Z \
  --text "Decision: Launch requires a clean trust gate report."
```

### Check freshness

```bash
total-recall knowledge freshness \
  --entity "launch requires a clean trust gate" \
  --category decision \
  --format text
```

### Inspect timeline

```bash
total-recall knowledge graph timeline \
  --entity "launch" \
  --at-time 2026-01-06T00:00:00Z
```

### Export and import

```bash
total-recall export --out total-recall-backup.tar.gz
total-recall import total-recall-backup.tar.gz --replace
```

Import rejects unsafe tar paths and validates manifest hashes before restoring.

### Back up on a schedule

```bash
total-recall backup run --out-dir ~/total-recall-backups --keep 14 --keep-days 90
total-recall backup status --out-dir ~/total-recall-backups
```

### Install for Hermes

```bash
total-recall hermes install --profile filippo --activate --format text
hermes -p filippo memory status
```

If running from a checkout:

```bash
./scripts/install_hermes_plugin.sh --profile filippo --activate --format text
```

## What Good Looks Like

A healthy store has:

- `verify` returns `ok: true` / `PASS`.
- Latest checkpoint event count equals current ledger event count, or lag is understood and acceptable for the task.
- Anchor exists for the latest checkpoint.
- Open incident count is zero or known/triaged.
- Derived index is fresh or rebuildable.
- Knowledge graph has cited entities/edges and zero uncited authority claims.
- Trust gate passes before release or handoff claims.
- Backup archive exists before machine migration or risky work.

## Fail-Closed Incidents

A fail-closed incident means Total Recall refused to pretend memory is safe.

Common causes:

- ledger event text/hash was modified
- event order changed
- checkpoint was edited
- checkpoint points at a different ledger hash
- anchor is missing or signature does not match
- trust gate required flow failed

Inspect:

```bash
total-recall incidents list
total-recall verify --format json
total-recall trust status --format json
```

Resolve only after the root cause is fixed and a fresh verify/trust gate passes.

## Safety Rules For Operators

- Do not edit `ledger/events.jsonl` by hand.
- Do not trust `index/` as source of truth.
- Do not ingest generated `reports/` back into memory.
- Do not authorize federation unless you intend to read another workspace.
- Do not send private memory to external providers without explicit authorization.
- Do not call a dashboard “remote MCP” production-ready until OAuth/scoped clients are implemented.

## Demo And Benchmark Links

- Demo guide: [demo-guide.md](demo-guide.md)
- Benchmarks: [benchmarks.md](benchmarks.md)
- Architecture: [knowledge-engine-architecture.md](knowledge-engine-architecture.md)
- Comparison: [total-recall-memory-layer-comparison-2026-06-03.md](total-recall-memory-layer-comparison-2026-06-03.md)
