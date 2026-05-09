# Total Recall

Standalone, local-first continuity memory for agent runtimes.

Total Recall is a framework-agnostic continuity engine. It keeps an append-only
ledger as the source of truth, reduces that ledger into deterministic state,
creates signed checkpoints, verifies integrity before rehydration, and builds
rebuildable local retrieval indexes for recall.

It does not depend on OpenClaw, OpenBrain, or Hermes. Hermes Agent can use it
through the optional provider plugin in `hermes-plugin/total-recall`.

## What It Provides

- append-only ledger ingestion with hash chaining
- deterministic state reduction
- checkpoint creation
- signed anchor verification
- fail-closed verify and rehydrate
- incident artifacts
- external-memory quarantine, promote, and reject flow
- derived LanceDB, QMD, and SQLite/FTS retrieval indexes with lexical fallback
- source-cited context planning
- optional Hermes Agent memory provider plugin

## Install For Local Development

```bash
git clone git@github.com:dax8it/total-recall.git
cd total-recall
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[semantic]'
python -m pytest -q
```

The `semantic` extra installs LanceDB. It is optional; SQLite/FTS and lexical
fallback work without it.

You can also run directly from the checkout:

```bash
PYTHONPATH=src python -m total_recall_core.cli health
./bin/total-recall health
```

## Storage

Set `TOTAL_RECALL_HOME` to choose the local store. If unset, the core uses a
profile-appropriate default supplied by the caller. In Hermes, the provider uses:

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
keys/anchor.ed25519
keys/anchor.ed25519.pub
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

QMD is optional and is discovered from `TOTAL_RECALL_QMD_BIN` or `PATH`.

Useful environment flags:

```bash
TOTAL_RECALL_ENABLE_LANCEDB=0
TOTAL_RECALL_ENABLE_QMD=0
TOTAL_RECALL_QMD_EMBED=1
```

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
total-recall doctor
total-recall export --out total-recall-backup.tar.gz
total-recall import total-recall-backup.tar.gz
total-recall incidents list
total-recall external ingest --source handoff.md --text "Imported context"
```

## Trust Model

The ledger is append-only JSONL with a hash chain. Checkpoints pin the reduced
state hash, event count, and last event hash. Anchors sign checkpoint hashes with
a local Ed25519 keypair stored at `keys/anchor.ed25519` and
`keys/anchor.ed25519.pub`. Legacy HMAC-SHA256 anchors remain verifiable for
older local stores, but new checkpoints use Ed25519.

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

See [docs/hermes.md](docs/hermes.md) for install, profile selection,
smoke-test, recovery, and troubleshooting commands.

Each Hermes profile gets its own store and derived indexes at
`$HERMES_HOME/total-recall`. Multiple agents can share the same core/plugin code
while keeping profile-local ledgers and indexes isolated.

## Hermes Compaction And Rehydration

Hermes Agent owns context compaction thresholds. Total Recall does not decide
when compaction happens.

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
python -m pytest -q
```
