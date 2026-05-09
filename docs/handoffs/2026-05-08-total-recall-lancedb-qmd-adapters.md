# Total Recall LanceDB + QMD Adapter Handoff

Date: 2026-05-08 America/New_York

## Status

Standalone Total Recall now supports three derived retrieval backends:

1. LanceDB local vector-ish index
2. QMD compatibility index
3. SQLite/FTS deterministic baseline

Authority remains unchanged:

- `ledger/events.jsonl`
- `state/current.json`
- `checkpoints/*.json`
- `anchors/*.json`

Derived indexes are never continuity authority. Verification rebuilds them from
the ledger after checkpoint/anchor checks.

## Paths

- Core: `/Users/fattyclaw/.openclaw/workspace/packages/total-recall-core`
- Hermes plugin: `/Users/fattyclaw/.hermes/plugins/total-recall`
- Filippo store: `/Users/fattyclaw/.hermes/profiles/filippo/total-recall`
- SQLite index: `/Users/fattyclaw/.hermes/profiles/filippo/total-recall/index/total_recall.sqlite`
- LanceDB index: `/Users/fattyclaw/.hermes/profiles/filippo/total-recall/index/lancedb/`
- QMD docs/meta: `/Users/fattyclaw/.hermes/profiles/filippo/total-recall/index/qmd-docs/`, `/Users/fattyclaw/.hermes/profiles/filippo/total-recall/index/qmd-meta.json`

## Agent Scope

This is not Filippo-only. The code is profile-agnostic.

Each Hermes agent/profile gets its own store under that profile's
`$HERMES_HOME/total-recall`, including its own derived indexes. Filippo, Sparky,
and Smarty can share the same core and plugin while keeping separate ledgers,
checkpoints, anchors, and indexes.

## Search Ladder

```text
LanceDB
QMD
SQLite/FTS
lexical fallback
```

Search results are source-cited back to ledger artifacts.

## Runtime Notes

- LanceDB was installed into the Hermes venv:
  `/Users/fattyclaw/.hermes/hermes-agent/.venv`
- QMD is discovered at:
  `/Users/fattyclaw/.bun/bin/qmd`
- CLI wrapper now prefers:
  1. `$TOTAL_RECALL_PYTHON`
  2. Hermes venv Python
  3. system `python3`

Useful toggles:

```bash
TOTAL_RECALL_ENABLE_LANCEDB=0
TOTAL_RECALL_ENABLE_QMD=0
TOTAL_RECALL_QMD_BIN=/path/to/qmd
TOTAL_RECALL_QMD_EMBED=1
```

## Validation

Core tests:

```text
9 passed
```

Live Filippo verify:

```text
verify: PASS
eventCount: 1705
open incidents: 0
sqlite-fts: fresh, 1705 docs
lancedb: fresh, 1705 docs
qmd: fresh, 1705 docs
```

Fresh checkpoint:

```text
checkpoint_filippo-main_20260509T015942Z_290bc0af
```

Fresh verify report:

```text
/Users/fattyclaw/.hermes/profiles/filippo/total-recall/reports/verify_filippo-main_20260509T015953Z.json
```

Smoke search:

```bash
total-recall --home /Users/fattyclaw/.hermes/profiles/filippo/total-recall search "StoryForge video engine adapter" --max-results 3
```

Result used:

```text
backend: derived-hybrid
backends: lancedb, qmd, sqlite-fts
errors: []
```

## Next Step For Sparky/Smarty

After their OAuth/model setup is stable:

```bash
sparky config set memory.provider total-recall
sparky memory status
smarty config set memory.provider total-recall
smarty memory status
```

Then run per-profile smoke:

```bash
TOTAL_RECALL_HOME=<profile-total-recall-home> total-recall health
TOTAL_RECALL_HOME=<profile-total-recall-home> total-recall index rebuild
TOTAL_RECALL_HOME=<profile-total-recall-home> total-recall search "StoryForge"
```
