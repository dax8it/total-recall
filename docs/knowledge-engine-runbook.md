# Total Recall Knowledge Engine Runbook

- Owner: Alex Covo / Total Recall maintainer
- Last updated: 2026-05-18
- Stale by: 2026-06-18
- Status: planned operations contract
- Related docs: [roadmap](knowledge-engine-roadmap.md), [decisions](knowledge-engine-decisions.md), [architecture](knowledge-engine-architecture.md), [risks](knowledge-engine-risks.md), [API](knowledge-engine-api.md), [tribal knowledge](knowledge-engine-tribal-knowledge.md)

This runbook defines the expected operator flows once the Knowledge Engine module exists. It is intentionally local-first and solo-project sized.

## Component Ownership

| Component | Owner | Critical behavior |
|---|---|---|
| Ledger/checkpoint/anchor core | Total Recall maintainer | Fail closed on integrity failure. |
| Knowledge Engine store | Total Recall maintainer | Rebuildable; never authority. |
| Graph extractor | Total Recall maintainer | No uncited active nodes/edges. |
| Reranker/provider boundary | Total Recall maintainer | Redacted/scope-filtered provider payloads. |
| Nightly synthesis | Total Recall maintainer | Atomic publish; last successful artifacts remain active on failure. |
| Hermes provider tools | Total Recall maintainer | Existing rehydrate and fail-closed behavior preserved. |
| Skills repo integration | Total Recall maintainer | Skills call CLI, not Python internals. |

## Install Or Provision A Workspace

Expected happy path:

```text
total-recall health
total-recall verify
total-recall knowledge index rebuild
total-recall knowledge graph rebuild
total-recall knowledge truth build
total-recall knowledge evaluate run
```

Exit criteria:
- Core `verify` passes.
- KE index is fresh for the ledger state.
- Graph validation reports no uncited active nodes/edges.
- Compiled truth projection is fresh for the current index state.
- Evaluation smoke passes for the workspace.

Governing decisions: [D-001](knowledge-engine-decisions.md#d-001-knowledge-engine-is-an-internal-total-recall-module), [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory), [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)

## Query During Agent Work

Hermes-Agent should use Knowledge Engine automatically when memory/history/continuity is relevant, not before every trivial task.

Expected command shape:

```text
total-recall knowledge query --query "<question>" --mode normal --format json
```

When user-facing claims depend on memory, cite the returned citations. Internal orientation use can be logged without visible citations.

For a human-readable ledger-derived brain view:

```text
total-recall knowledge truth show --format md
```

For relationship context without trusting uncited graph assertions:

```text
total-recall knowledge graph inspect --entity "<name>"
total-recall knowledge graph traverse --entity "<name>" --depth 2
```

Governing decisions: [D-008](knowledge-engine-decisions.md#d-008-default-query-output-is-a-synthesized-answer-with-citations), [D-015](knowledge-engine-decisions.md#d-015-confidence-modes-gate-answer-behavior), [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge)

## Nightly Synthesis

Expected scheduler contract:

```text
total-recall knowledge synthesize run
```

Scheduler examples can use cron, launchd, CI, or any local runner. The command is scheduler-agnostic.

Required behavior:
1. Read source artifacts.
2. Stage synthesis artifacts.
3. Validate citations, scopes, and evidence hashes.
4. Atomically publish complete artifacts.
5. If validation fails, keep last successful artifacts active and write a failed-run report.

Governing decision: [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional)

## Promotion

Only the owner can promote provisional synthesis into canonical memory.

Expected command shape:

```text
total-recall knowledge synthesize promote <proposal-id>
```

Promotion must create a normal ledger event through the existing append path. Derived synthesis files remain rebuildable.

Governing decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional)

## Federation

Default behavior:
- One index per workspace.
- Federation only when explicitly requested and authorized.
- Results are workspace-separated by default.
- Cross-workspace synthesis requires explicit authorization.

Local-only query:

```text
total-recall knowledge query --query "<question>" --mode normal --format json
```

Explicit read-only federation:

```text
total-recall knowledge query \
  --query "<question>" \
  --federate /path/to/other/workspace-or-home \
  --authorize-federation \
  --format json
```

If `--federate` is provided without `--authorize-federation`, the query remains local and the JSON response records `federation.status = AUTHORIZATION_REQUIRED`. Authorized federation requires each federated workspace to have a fresh Knowledge Engine index; the current implementation does not rebuild another workspace as a side effect of querying it.

Provider payload audit reports are written to `$TOTAL_RECALL_HOME/knowledge/providers/`. Inspect these when debugging scope, provider, or federation behavior; reports hash the query/session and omit raw memory text.

Governing decision: [D-016](knowledge-engine-decisions.md#d-016-multi-workspace-support-is-read-first-with-explicit-federation)

## External Provider Adapters

Default behavior:
- Local hash rerank remains active without external services.
- External providers are optional and skipped unless explicitly authorized per query.
- Authorized but unconfigured providers degrade without changing the local answer path.
- Provider reports log status, scopes, authorization, and latency without raw private memory text.

Dry request that remains local:

```text
total-recall knowledge query \
  --query "<question>" \
  --external-provider hindsight \
  --format json
```

Explicit redacted/minimized external-provider attempt:

```text
total-recall knowledge query \
  --query "<question>" \
  --external-provider hindsight \
  --authorize-external-provider \
  --format json
```

Current implementation returns provider status `SKIPPED` without authorization and `UNAVAILABLE` when authorized but no adapter is configured. This keeps the contract provider-agnostic while preserving local-first recall.

Governing decisions: [D-007](knowledge-engine-decisions.md#d-007-reranking-is-pluggable-provider-agnostic-and-local-first), [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default)

## Failure Handling

| Failure | Operator response | Governing decisions |
|---|---|---|
| Ledger/checkpoint/anchor failure | Stop. Do not query or synthesize trusted answers. Restore or repair core continuity first. | [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory) |
| KE index stale | Rebuild from source artifacts. | [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace), [D-005](knowledge-engine-decisions.md#d-005-sqlite-fts5-is-the-local-first-knowledge-engine-store) |
| Graph has uncited node/edge | Fail graph validation; quarantine offending items; do not use in normal answers. | [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked) |
| Provider unavailable | Degrade to FTS + graph; fail closed only if confidence threshold cannot be met. | [D-006](knowledge-engine-decisions.md#d-006-embeddings-are-optional-and-advisory), [D-007](knowledge-engine-decisions.md#d-007-reranking-is-pluggable-provider-agnostic-and-local-first), [D-015](knowledge-engine-decisions.md#d-015-confidence-modes-gate-answer-behavior) |
| Synthesis fails halfway | Keep last successful artifacts active; inspect failed-run report. | [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional) |
| Scope leak suspected | Disable federation/provider calls; run scope leak evaluation fixtures; inspect provider-call reports. | [D-012](knowledge-engine-decisions.md#d-012-scope-enforcement-is-private-by-default), [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default), [D-016](knowledge-engine-decisions.md#d-016-multi-workspace-support-is-read-first-with-explicit-federation) |
| Secret appears in derived artifact/report | Treat as incident; redact; rebuild derived artifacts; add regression fixture. | [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time) |

## Release Gate

Before stable V1:

```text
python scripts/privacy_scan.py
python -m pytest -q
scripts/install_smoke.sh
total-recall knowledge evaluate run
total-recall knowledge evaluate scorecard
```

Stable V1 cannot be declared complete unless:
- all required KE layers score at least 7/10
- no existing Total Recall core gate regresses
- provider payload reports show no unauthorized private scope
- synthetic and redacted Hermes smoke tests pass

Governing decision: [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)

## Export/Delete/Rebuild

Owner workflows:
- Export Total Recall artifacts through the existing export path.
- Delete/rebuild derived `knowledge/` artifacts without touching the ledger.
- Treat canonical event deletion as advanced/destructive and require backup plus audit note.

Governing decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory)
