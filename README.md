# total-recall-core

Framework-agnostic Total Recall continuity engine.

This repository is standalone. It does not depend on OpenClaw/OpenBrain and is
intended to be consumed directly by Hermes Agent or any other local agent
runtime.

This package is the local authority for:

- append-only ledger ingestion
- deterministic state reduction
- checkpoint creation
- signed anchor verification
- fail-closed verify and rehydrate
- incident artifacts
- external-memory quarantine, promote, and reject flow
- derived LanceDB, QMD, and SQLite/FTS retrieval indexes with lexical fallback
- source-cited context planning

It does not call OpenClaw/OpenBrain and does not import Hermes. Hermes consumes it through the `total-recall` memory provider plugin.

## Storage

Set `TOTAL_RECALL_HOME` to choose the store. In Hermes, the provider defaults to:

```text
$HERMES_HOME/total-recall
```

Directory layout:

```text
ledger/events.jsonl
state/current.json
checkpoints/*.json
anchors/*.json
reports/*.json
reports/*.md
incidents/*.json
external-memory/{inbox,quarantine,promoted,rejected}/
index/total_recall.sqlite
index/lancedb/
index/lancedb-meta.json
index/qmd-docs/
index/qmd-meta.json
keys/anchor.key
```

All files under `index/` are derived retrieval caches. They are rebuilt from
`ledger/events.jsonl`; they are never the authority for continuity, checkpoint,
or rehydrate decisions.

Search uses this ladder:

```text
LanceDB vector-ish local index
QMD compatibility index
SQLite/FTS deterministic index
lexical authority-artifact scan
```

LanceDB is optional and can be installed with:

```bash
pip install 'total-recall-core[semantic]'
```

QMD is optional and is discovered from `TOTAL_RECALL_QMD_BIN` or `PATH`.
Set `TOTAL_RECALL_ENABLE_LANCEDB=0` or `TOTAL_RECALL_ENABLE_QMD=0` to disable
those adapters. Set `TOTAL_RECALL_QMD_EMBED=1` to ask QMD to build vector
embeddings after collection rebuilds.

## CLI

```bash
total-recall health
total-recall ingest --kind note --text "Remember this." --session-id main
total-recall search "Remember"
total-recall index status
total-recall index rebuild
total-recall index rebuild --backend lancedb
total-recall index rebuild --backend qmd
total-recall checkpoint --session-id main
total-recall verify --session-id main
total-recall rehydrate --session-id main --query "Remember"
total-recall incidents list
total-recall external ingest --source handoff.md --text "Imported context"
```

## Trust Model

The ledger is append-only JSONL with a hash chain. Checkpoints pin the reduced state hash, event count, and last event hash. Anchors sign checkpoint hashes using a local HMAC-SHA256 key stored at `keys/anchor.key`.

Verification fails closed when:

- a ledger event hash is invalid
- the ledger hash chain is broken
- checkpoint hash mismatches
- reduced state differs from checkpoint
- anchor is missing
- anchor checkpoint hash mismatches
- anchor signature mismatches

During verification, Total Recall rebuilds derived indexes from the ledger after
the authoritative checks. A tampered or stale derived index is overwritten from
trusted ledger state rather than trusted directly.

## Hermes Setup

The Hermes provider lives at:

```text
hermes-plugin/total-recall
```

For a profile-scoped Hermes home, make that plugin available under the profile's
documented memory-provider path, then select:

```bash
ln -s /Users/Shared/GITHUB/total-recall/hermes-plugin/total-recall "$HERMES_HOME/plugins/memory/total-recall"
hermes -p total-recall-smoke config set memory.provider total-recall
hermes -p total-recall-smoke memory status
```

Only switch a live profile after `health`, `search`, `checkpoint`, `verify`, and `rehydrate` pass.

Each Hermes profile gets its own store and derived indexes at
`$HERMES_HOME/total-recall`. Filippo, Sparky, and Smarty can share the same
core/plugin code while keeping profile-local ledgers and indexes isolated.

## Hermes Compaction And Rehydration

Hermes Agent owns context compaction thresholds. Total Recall does not currently
decide when compaction happens.

Hermes defaults:

```yaml
compression:
  enabled: true
  threshold: 0.50
  target_ratio: 0.20
  protect_last_n: 20
```

When Hermes approaches the threshold, it calls the active memory provider's
`on_pre_compress(messages)` hook before summarizing and discarding older context.
The Total Recall Hermes provider responds by:

1. ingesting a `pre_compress` event into the authoritative ledger
2. building a source-cited context plan from the local store
3. returning that block to Hermes so the compressor can preserve durable
   decisions, blockers, file paths, approvals, and next actions
4. recording the Hermes compression-driven `session_switch` after Hermes rotates
   the session id

Regular completed turns are ingested through `sync_turn()`. Session exits create
a `session_end` event and a checkpoint. Rehydration is explicit and fail-closed:

```bash
total-recall verify --session-id main
total-recall rehydrate --session-id main --query "active continuity"
```

`rehydrate` first runs verification. If the ledger, reduced state, checkpoint, or
anchor fails validation, Total Recall refuses to produce a context block.

## Automatic Rehydration

The Hermes provider can automatically inject a verified rehydrate block when
continuity risk rises. This is provider policy layered on top of Hermes
compaction; Hermes still owns compaction thresholds.

Default policy:

```yaml
memory:
  total-recall:
    auto_rehydrate:
      enabled: true
      context_threshold: 0.70
      cooldown_seconds: 180
      startup_cooldown_seconds: 900
      compression_count_threshold: 2
      stale_check_every_turns: 5
      max_chars: 5000
```

Automatic triggers:

- Hermes startup or gateway restart
- `/new`
- `/resume`
- branch/session id changes
- after compaction
- after repeated compactions in one session
- context usage crossing 70 percent
- stale checkpoint detection
- low local continuity confidence during prefetch

The provider stores cooldown state at:

```text
$HERMES_HOME/total-recall/state/auto_rehydrate.json
```

Automatic rehydrate still fails closed. If verification fails, the injected block
is a short warning that asks the agent to use `total_recall_verify` before
trusting prior continuity.

## Future Add-Ons

External semantic memory adapters are deferred. They should be implemented as
derived-memory bridges, not as replacements for the local authority.

Planned candidates:

- Hindsight adapter for retain, recall, reflect, entity extraction, and synthesis
- Honcho adapter for user/agent peer modeling, cards, context, and conclusions
- Mem0 adapter for fact extraction, dedupe, profile recall, and semantic search

Trust boundary:

```text
external adapter result
-> Total Recall external-memory quarantine
-> review/promote/reject
-> promoted item becomes a ledger event
-> checkpoint/anchor verification covers the promoted truth
```

Adapters must never write directly to authoritative state. The ledger,
checkpoints, and anchors remain the source of truth; external systems can only
produce cited candidates and receipts.

## Test

```bash
cd /Users/fattyclaw/.openclaw/workspace/packages/total-recall-core
PYTHONPATH=src /Users/fattyclaw/.hermes/hermes-agent/.venv/bin/python -m pytest -q
```
