# Total Recall Hermes Memory Provider

Total Recall is a local-first continuity authority for Hermes Agent. It keeps
an append-only ledger, signed checkpoints, fail-closed rehydrate, cited recall,
compiled truth, source ingest, freshness checks, temporal graph timelines,
explicit federation, and an evidence-locked Knowledge Engine.

## Tools

The provider exposes Total Recall search/status/checkpoint/verify/trust-verify/learning-review/rehydrate,
working-context source ingest, cited Knowledge Engine query, freshness,
compiled truth, graph inspect/timeline, and explicit federation query tools.
Federation requires explicit authorization and returns workspace-separated
results rather than silently merging another agent's memory. Learning review
returns candidate cards, layer-routing decisions, action boundaries, and a
wake-up diff without mutating the ledger.

## Compaction And Rehydration In Plain English

Use one operator model:

```text
save completed turns -> checkpoint -> verify -> rehydrate cited context
```

Hermes owns when old chat is compacted. Total Recall owns whether memory is safe
to reuse after that context changes. The easiest profile policy is to align the
Hermes compaction threshold and Total Recall auto-rehydrate threshold so both are
one visible **context risk zone**:

```yaml
compression:
  enabled: true
  threshold: 0.55

memory:
  provider: total-recall
  total-recall:
    auto_rehydrate:
      enabled: true
      context_threshold: 0.55
```

If the Total Recall threshold is higher than the Hermes compaction threshold,
treat it as an extra high-context safety net, not a separate memory authority.
The provider still handles Hermes compaction hooks.

What gets saved:

- completed turns through `sync_turn()`
- pre-compaction continuity through `on_pre_compress(messages)`
- session switches/resets/resumes as lifecycle events
- session end plus checkpoint on shutdown
- explicit checkpoint events when Hermes or the user calls the checkpoint tool

Search and rehydrate examples:

```bash
total-recall rehydrate --session-id main --query "active work before compaction"
total-recall search "Total Recall dashboard backup panel"
total-recall knowledge query --query "What was the last verified state before rehydrate?" --format text
total-recall knowledge query --query "What decisions did we make about backup freshness?" --format text
```

## Install

Preferred:

```bash
total-recall hermes install --profile <profile> --activate --format text
total-recall hermes doctor
```

The installer detects Hermes' Python environment, installs or upgrades
`total-recall-core` there when needed, writes the plugin bundle to
`~/.hermes/plugins/memory/total-recall`, also writes the flat
`~/.hermes/plugins/total-recall` compatibility provider path used by Hermes
v0.15.x, selects it as the profile's memory provider, writes aligned Context Risk
Zone defaults (`compression.threshold=0.55`, `auto_rehydrate.enabled=true`,
`auto_rehydrate.context_threshold=0.55`), and verifies Hermes memory status.

Manual fallback:

```bash
mkdir -p ~/.hermes/plugins/memory
cp -R total-recall ~/.hermes/plugins/memory/total-recall
cp -R total-recall ~/.hermes/plugins/total-recall
hermes -p <profile> config set memory.provider total-recall
hermes -p <profile> memory status
```

The Python package `total-recall-core` must be importable in the Python
environment used by Hermes. If auto-detection cannot find that interpreter,
pass `--hermes-python /path/to/hermes/venv/bin/python`.
