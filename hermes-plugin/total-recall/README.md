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
v0.15.x, selects it as the profile's memory provider, and verifies Hermes
memory status.

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
