# Total Recall SQLite/FTS Derived Index Handoff

Date: 2026-05-08 America/New_York

## Status

Standalone Total Recall now has a portable SQLite/FTS retrieval index.

- Core path: `/Users/fattyclaw/.openclaw/workspace/packages/total-recall-core`
- Hermes plugin: `/Users/fattyclaw/.hermes/plugins/total-recall`
- Filippo store: `/Users/fattyclaw/.hermes/profiles/filippo/total-recall`
- Index DB: `/Users/fattyclaw/.hermes/profiles/filippo/total-recall/index/total_recall.sqlite`
- Core version: `1.1.0`
- Authority remains: ledger + reduced state + checkpoints + anchors
- Index is derived only and rebuilt from `ledger/events.jsonl`

## What Changed

- Added deterministic SQLite/FTS index builder.
- Added index freshness metadata:
  - schema
  - built_at
  - event_count
  - last_event_hash
  - state_hash
  - authority/source labels
- Search now uses SQLite/FTS when fresh.
- Search rebuilds the index from the ledger if stale.
- Search falls back to lexical artifact scan if the derived index cannot be used.
- Verification now rebuilds the index from the ledger after authoritative checkpoint/anchor checks.
- A tampered/stale index is overwritten from trusted ledger state, not trusted directly.
- Atomic JSON writes now use unique temporary filenames, so parallel health/search/index operations do not collide on `state/current.json` writes.

## CLI

```bash
total-recall index status
total-recall index rebuild
total-recall index search "video engine adapter"
total-recall search "video engine adapter"
```

For Filippo explicitly:

```bash
/Users/fattyclaw/.local/bin/total-recall --home /Users/fattyclaw/.hermes/profiles/filippo/total-recall index status
/Users/fattyclaw/.local/bin/total-recall --home /Users/fattyclaw/.hermes/profiles/filippo/total-recall index rebuild
```

## Validation Performed

Core tests:

```text
7 passed
```

Live Filippo smoke:

```text
index backend: sqlite-fts
eventCount: 1703
documentCount: 1704
fresh: true
openIncidents: 0
verify: PASS
```

Fresh post-handoff checkpoint:

```text
checkpoint_filippo-main_20260509T013902Z_173c9ef9
```

Fresh verify report:

```text
/Users/fattyclaw/.hermes/profiles/filippo/total-recall/reports/verify_filippo-main_20260509T013908Z.json
```

Gateway restart:

```text
filippo gateway restart
Service restarted
```

Hermes memory provider after restart:

```text
Provider: total-recall
Plugin: installed
Status: available
total-recall (local) active
```

## Design Rule

Do not treat `index/total_recall.sqlite` as continuity authority. It is a fast derived retrieval store only. If it is missing, stale, corrupt, or tampered, rebuild it from the ledger. Checkpoint and rehydrate trust the ledger/checkpoint/anchor path.

## Next Optional Upgrade

Add a QMD adapter later behind the same derived-index contract. QMD should be another rebuildable retrieval backend, never the source of truth.
