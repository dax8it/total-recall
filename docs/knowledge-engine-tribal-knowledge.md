# Total Recall Knowledge Engine Tribal Knowledge

- Owner: Alex Covo / Total Recall maintainer
- Last updated: 2026-05-18
- Stale by: 2026-06-18
- Status: current-state notes before implementation
- Related docs: [roadmap](knowledge-engine-roadmap.md), [decisions](knowledge-engine-decisions.md), [architecture](knowledge-engine-architecture.md), [risks](knowledge-engine-risks.md), [API](knowledge-engine-api.md), [runbook](knowledge-engine-runbook.md)

This document captures context that is not obvious from code alone. It should prevent future agents from "fixing" deliberate constraints.

## Historical Context

- Total Recall began as a deterministic continuity layer, not a semantic knowledge engine.
- The system's core value is fail-closed recall: ledger events reduce into state, checkpoints pin the state, anchors sign checkpoints, and verification gates rehydrate.
- Existing search is already derived and rebuildable. It uses a ladder: LanceDB when available, QMD when available, SQLite/FTS, then lexical fallback.
- Generated reports are deliberately excluded from retrieval to avoid recursive recall of recall output.
- External-memory items already use quarantine/promote/reject; this is the correct precedent for Knowledge Engine synthesis proposals.
- Hermes owns compaction thresholds. Total Recall responds through provider hooks and rehydrate policy.

## Undocumented Assumptions Now Made Explicit

| Assumption | Status | Governing decision |
|---|---|---|
| Derived indexes are never authority. | Keep. | [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority) |
| Store root is `$TOTAL_RECALL_HOME`, not a nested `memory/` directory. | Keep; normalize KE path to `knowledge/`. | [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace) |
| Missing scope should be treated as private. | Keep/extend. | [D-012](knowledge-engine-decisions.md#d-012-scope-enforcement-is-private-by-default) |
| Model/provider output is candidate evidence, not truth. | Keep/extend. | [D-006](knowledge-engine-decisions.md#d-006-embeddings-are-optional-and-advisory), [D-007](knowledge-engine-decisions.md#d-007-reranking-is-pluggable-provider-agnostic-and-local-first), [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked) |
| Owner promotion is safer than autonomous agent promotion. | Keep for V1. | [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional) |

## Current Debt And Drift

| Area | Current state | Drift or debt | Impact |
|---|---|---|---|
| Core module size | `api.py` owns many unrelated responsibilities. | Knowledge Engine will worsen this if added directly. | Extract a boundary before adding graph/synthesis logic. |
| CLI output | CLI prints JSON for every command. | Brief requires `--format json|md|text`. | Add format handling for KE commands without breaking current CLI. |
| Scope vocabulary | Code allows `shared_team`; brief uses `public`. | Need compatibility alias or migration path. | Prevents reading old memories incorrectly. |
| Derived path | Current derived retrieval uses `index/`; brief used `memory/knowledge/`. | Use `$TOTAL_RECALL_HOME/knowledge/` for KE and keep `index/`. | Avoids breaking current search. |
| Embeddings | Current LanceDB embedding is hash-based bag-of-words. | Useful as local placeholder, not equivalent to provider embeddings. | Scorecard must not overclaim semantic quality. |
| Reports | Reports are generated and excluded from retrieval. | KE reports must keep that pattern unless promoted. | Avoid recursive memory contamination. |
| Skills repo | Skills are generic engineering skills; no Total Recall KE skill yet. | Need additive skill or instructions. | Hermes and other agents need a stable usage contract. |

## Change-Impact Matrix

| Change area | Touches | Must not break | Required checks |
|---|---|---|---|
| CLI namespace | `cli.py`, package entry point, docs, smoke tests | Existing `search`, `verify`, `rehydrate`, `backup`, `dashboard` commands | CLI tests, install smoke |
| KE store layout | Core config/layout, export/import policy, doctor | Existing `$TOTAL_RECALL_HOME/index/` behavior | store-layout tests, export/import tests |
| Graph extraction | KE source reader, sanitizer, graph tables, reports | Ledger verification and state reduction | graph provenance tests, rebuild tests |
| Reranking/providers | provider contract, query planner, redaction logs | Local/offline recall path | provider-unavailable tests, redaction tests |
| Synthesis | scheduler command, staging, publish, promotion | Ledger authority and external-memory queue semantics | failed-run test, owner-promotion test |
| Hermes tools | Hermes provider schemas, handler, system prompt block | Existing lifecycle hooks and fail-closed auto-rehydrate | plugin lifecycle tests |
| Skills repo | skill docs/instructions, ADR | Generic skill repo conventions | manual skill path check, doc link check |
| Evaluation | fixtures, scorecards, reports | Fortification scorecard meaning | before/after report test, release gate test |

## Terms

**Knowledge Engine**: Internal Total Recall module that adds graph, reranking, synthesis, and scorecard-backed intelligent recall.

**Ledger**: Append-only JSONL event chain under `$TOTAL_RECALL_HOME/ledger/events.jsonl`.

**Derived artifact**: Any index, graph, synthesis, report, or provider receipt that can be rebuilt from ledger/source artifacts.

**Promotion**: Owner-approved act of turning a provisional external/synthesis item into a canonical ledger event.

**Scope**: Per-record visibility boundary such as private, internal, group-safe, public, or legacy shared-team.

**Federation**: Explicit authorized query over multiple workspace-local indexes, returned workspace-separated by default.

## Non-Obvious Rules For Future Implementers

- Do not store raw provider prompts containing private memory in reports.
- Do not bulk-index transcripts to improve recall scores.
- Do not let synthesis directly write canonical facts.
- Do not merge federated workspaces silently.
- Do not treat high reranker score as sufficient proof.
- Do not make optional semantic dependencies required for core install smoke.
- Do not add local absolute paths to docs or generated reports; the privacy scan is expected to catch them.
