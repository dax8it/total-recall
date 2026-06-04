# Demo Guide

This guide is for showing Total Recall to someone who does not already know why agent memory needs a continuity authority.

The story to tell:

> Agents do not just need to remember. They need to prove which memory is allowed to matter after restarts, compaction, imports, and handoffs.

## Demo Assets In This Repo

- Architecture diagram: [assets/total-recall-architecture.svg](assets/total-recall-architecture.svg)
- Trust spine diagram: [assets/trust-spine.svg](assets/trust-spine.svg)
- Benchmarks: [benchmarks.md](benchmarks.md)
- Operational manual: [operational-manual.md](operational-manual.md)
- Dashboard docs: [backup-dashboard.md](backup-dashboard.md)
- Comparison: [total-recall-memory-layer-comparison-2026-06-03.md](total-recall-memory-layer-comparison-2026-06-03.md)

## Five-Minute Video Script

### Shot 1: The problem

Show a normal agent memory stack as a sentence:

```text
chat history + vector search + notes != continuity authority
```

Say:

> A normal memory provider can retrieve text. It usually cannot prove that text survived restart, compaction, import/export, or tamper without silent drift.

### Shot 2: Where Total Recall lives

Show `assets/total-recall-architecture.svg`.

Say:

> Total Recall sits between the agent runtime and memory. The ledger is authority. Search indexes, graph, compiled truth, reports, and dashboards are projections that can be rebuilt.

### Shot 3: Create memory and checkpoint

```bash
export TOTAL_RECALL_HOME=$(mktemp -d)
total-recall ingest --kind note --session-id demo --text "Decision: memory must verify before rehydrate."
total-recall checkpoint --session-id demo --label demo-checkpoint
total-recall verify --session-id demo
```

Say:

> The checkpoint signs the current reduced ledger state. If the ledger or checkpoint changes, verify fails closed.

### Shot 4: Rehydrate with citations

```bash
total-recall rehydrate --session-id demo --query "verify before rehydrate"
```

Say:

> Rehydrate is not just search. It verifies first, then returns cited context.

### Shot 5: The dashboard

```bash
total-recall dashboard --backup-dir ~/total-recall-backups
```

Open the printed local URL.

Show:

- Trust Spine
- Knowledge Engine
- Operator Workbench
- Obsidian Vault Export
- Remote Backup Providers

Click `Trust Gate`.

Say:

> The dashboard is the operator surface: trust, incidents, knowledge, source ingest, vault export, backup posture, and release gate checks.

### Shot 6: Benchmark proof

```bash
PYTHONPATH=src .venv/bin/python scripts/benchmark_total_recall.py --events 250 --queries 25
```

Show `reports/benchmarks/benchmark_latest.md`.

Say:

> The benchmark does not only time retrieval. It proves ingest, checkpoint, verify, search, Knowledge Engine query, rehydrate, export/import, and tamper detection.

### Shot 7: Differentiation

Show `assets/trust-spine.svg` or the comparison doc.

Say:

> Total Recall is not trying to be the biggest brain repo or fastest vector DB. It is a local continuity authority: append-only ledger, signed checkpoints, fail-closed rehydrate, cited recall, freshness, temporal graph, explicit federation, and an operator dashboard.

## One-Minute Terminal Demo

```bash
export TOTAL_RECALL_HOME=$(mktemp -d)
total-recall ingest --kind note --session-id demo --text "Promise: Total Recall refuses corrupted memory."
total-recall checkpoint --session-id demo --label demo
total-recall trust verify --format text
total-recall rehydrate --session-id demo --query "corrupted memory"
```

What to say:

> If this passes, Total Recall has a signed checkpoint, verified ledger, and cited rehydrate path. If it fails, it creates evidence instead of pretending memory is safe.

## Dashboard Demo Checklist

Before recording:

```bash
export TOTAL_RECALL_HOME=$(mktemp -d)
total-recall sources ingest \
  --type meeting \
  --title "Dashboard Demo" \
  --occurred-at 2026-01-01T00:00:00Z \
  --text "Decision: Dashboard demo should show trust gate, cited recall, freshness, and graph timeline."
total-recall checkpoint --session-id dashboard-demo --label dashboard-demo
total-recall dashboard --port 8899 --backup-dir ~/total-recall-backups
```

In the browser:

1. Confirm `Remote MCP Admin Control Center` renders.
2. Click `Trust Gate`.
3. Run a Workbench query for `dashboard demo trust gate`.
4. Switch to Freshness and search `dashboard demo`.
5. Switch to Graph and inspect `dashboard demo`.
6. Export an Obsidian vault into a temporary folder if you want to show derived notes.
7. Keep backup upload disabled unless you intentionally configured a backup target.

## Recording Tips

- Keep `TOTAL_RECALL_HOME` pointed at a temporary synthetic store.
- Do not record a private profile store.
- Use large terminal font and narrow commands.
- Show `verify`/`trust verify` outputs as proof, not marketing narration.
- If a command fails, leave it in the demo and explain fail-closed behavior.

## Suggested Diagrams For Slides

1. Architecture: agent runtime -> Total Recall provider -> ledger/checkpoints/anchors -> derived indexes/knowledge/dashboard.
2. Trust Spine: ingest -> state -> checkpoint -> anchor -> verify -> rehydrate.
3. Memory provider comparison: retrieval memory vs continuity authority.
4. Demo benchmark flow: ingest -> checkpoint -> verify -> query -> rehydrate -> export/import -> tamper detection.

The first two diagrams are included under `docs/assets/`.
