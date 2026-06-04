# Hermes Total Recall Report Exclusion Repair - 2026-05-11

## Problem

Hermes profile `filippo` repeatedly hung or paused for very long periods during
startup, Telegram replies, and preflight compression. This was not primarily an
OpenAI outage. The active Total Recall profile had grown to about 142 GB because
generated `verify` and `rehydrate` reports were being searched and recalled into
later rehydrate reports.

The failure mode was self-referential retrieval:

1. Total Recall wrote generated reports under `total-recall/reports/`.
2. Lexical fallback retrieval scanned `reports/*.json`.
3. Rehydrate output included prior report payloads and search payloads.
4. New reports embedded old reports, causing recursive growth and slow startup.

## Operational Repair Already Applied

Generated reports were moved out of the active retrieval tree:

```text
~/.hermes-archives/total-recall/filippo/
```

The active profile was reduced to about 72 MB:

```text
~/.hermes/profiles/filippo/total-recall
```

The following active memory stores were preserved:

```text
index/
ledger/
state/
checkpoints/
anchors/
```

Only generated report artifacts were moved. Memory was not deleted.

A helper command was added:

```bash
hermes-total-recall-quarantine-reports filippo
```

It moves generated files from:

```text
~/.hermes/profiles/filippo/total-recall/reports/
```

to timestamped archives under:

```text
~/.hermes-archives/total-recall/filippo/
```

## Code Patch

Total Recall now excludes `total-recall/reports/**` from retrieval automatically.

Changed files:

```text
src/total_recall_core/api.py
hermes-plugin/total-recall/__init__.py
tests/test_core.py
README.md
```

Main behavior change:

- `search()` lexical fallback still searches authoritative ledger events.
- It still includes incidents and checkpoints as authority artifacts.
- It no longer scans `reports/*.json`.
- Generated reports remain available for explicit status, export, backup, and
  manual inspection workflows.

Hermes plugin description was updated so `total_recall_search` no longer claims
reports are searchable memory.

## Regression Coverage

Added test:

```text
test_generated_reports_are_not_retrieval_sources
```

The test writes a fake rehydrate report containing a unique phrase, searches for
that phrase, and asserts the report is not returned as retrieval context.

Verification performed:

```bash
uv run --with pytest pytest tests/test_core.py -q
# 21 passed, 1 skipped

uv run --with pytest pytest tests/test_hermes_plugin.py -q
# 4 passed
```

## Current Rule

Reports are audit artifacts, not memory. Keep distilled memory in the ledger,
state, checkpoints, anchors, and derived indexes. Keep generated report
transcripts in cold archive storage or active `reports/` for explicit inspection
only. They must not be part of automatic search, context planning, or rehydrate
retrieval.
