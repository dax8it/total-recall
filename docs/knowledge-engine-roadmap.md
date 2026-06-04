# Total Recall Knowledge Engine Roadmap

- Owner: Alex Covo / Total Recall maintainer
- Last updated: 2026-05-18
- Stale by: 2026-06-18
- Status: build-ready planning
- Related docs: [decisions](knowledge-engine-decisions.md), [risks](knowledge-engine-risks.md), [architecture](knowledge-engine-architecture.md), [API](knowledge-engine-api.md), [tribal knowledge](knowledge-engine-tribal-knowledge.md), [runbook](knowledge-engine-runbook.md)

This roadmap is ordered for implementation. Each step has exit criteria, decision references, and required tests. Stable V1 is not complete until the final build-readiness walk at the end of this document remains clean.

## Milestone M0: Current-State Baseline And Planning Lock

Purpose: make structural drift explicit before code changes.

Exit criteria:
- Architecture, decisions, risks, API, tribal knowledge, and runbook docs exist.
- Current dependency graph is documented from actual imports.
- Drift between the confirmed brief and current repo layout is flagged.
- Dirty worktree implementation files are not overwritten.

Risks: [R-007](knowledge-engine-risks.md#r-007-backward-compatibility-or-history-loss), [R-012](knowledge-engine-risks.md#r-012-current-store-path-drift)

### Steps

1. Document current entry points and dependency graph.
   - Decisions: [D-001](knowledge-engine-decisions.md#d-001-knowledge-engine-is-an-internal-total-recall-module), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory)
   - Exit criteria: architecture doc maps CLI, core API, dashboard, Hermes provider, scripts, tests, and store.
   - Required tests: none; doc validation by import scan.

2. Record load-bearing decisions and revisit triggers.
   - Decisions: all decisions in [decisions](knowledge-engine-decisions.md)
   - Exit criteria: every roadmap step references existing decision IDs.
   - Required tests: build-readiness walk resolves all decision references.

3. Record risks with owners and monitoring signals.
   - Decisions: [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)
   - Exit criteria: every risk has owner, blast radius, mitigation, and monitoring signal.
   - Required tests: none; doc review.

## Milestone M1: Knowledge Engine Shell, Store Layout, And CLI Namespace

Purpose: create the internal module boundary without changing authoritative memory behavior.

Exit criteria:
- `total-recall knowledge ...` exists behind the existing CLI.
- `$TOTAL_RECALL_HOME/knowledge/` layout exists and is disposable/rebuildable.
- Existing commands and tests still pass.
- CLI supports planned output formats for KE commands.

Risks: [R-007](knowledge-engine-risks.md#r-007-backward-compatibility-or-history-loss), [R-011](knowledge-engine-risks.md#r-011-dependency-bloat-undermines-local-first-install), [R-012](knowledge-engine-risks.md#r-012-current-store-path-drift)

### Steps

1. Add the Knowledge Engine internal module boundary under the existing core package.
   - Decisions: [D-001](knowledge-engine-decisions.md#d-001-knowledge-engine-is-an-internal-total-recall-module), [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge)
   - Exit criteria: core can instantiate KE services without Hermes-specific imports.
   - Required tests: import test; base `python -m pytest -q`.

2. Add `$TOTAL_RECALL_HOME/knowledge/` creation and status reporting.
   - Decisions: [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory)
   - Exit criteria: layout is created without touching existing event files; deleting `knowledge/` does not affect verify.
   - Required tests: store-layout test; rebuild-after-delete test.

3. Add `total-recall knowledge status` and format handling.
   - Decisions: [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge)
   - Exit criteria: `--format json|md|text` works for KE status; existing CLI output remains compatible.
   - Required tests: CLI output tests.

4. Update install smoke to include non-destructive KE status.
   - Decisions: [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory), [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)
   - Exit criteria: fresh install smoke still passes without optional provider packages.
   - Required tests: `scripts/install_smoke.sh`.

## Milestone M2: Source Reader, Sanitizer, And KE Index

Purpose: build the source and index foundation for graph and reranking.

Exit criteria:
- KE index rebuilds from current artifacts only.
- Malformed artifacts use severity-based handling.
- Secret redaction and injection tagging occur before derived writes.
- No raw transcripts are bulk-indexed.

Risks: [R-001](knowledge-engine-risks.md#r-001-cross-scope-leakage), [R-003](knowledge-engine-risks.md#r-003-provider-data-exposure), [R-009](knowledge-engine-risks.md#r-009-prompt-injection-through-memory-content)

### Steps

1. Implement source enumeration for ledger events, reduced state, rollups when present, and existing synthesis artifacts.
   - Decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-003](knowledge-engine-decisions.md#d-003-source-policy-is-spine-first), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory)
   - Exit criteria: reports and skill docs are excluded unless promoted; transcripts/handoffs are citation fallback only.
   - Required tests: source-policy tests; report-exclusion regression.

2. Implement redaction and injection tagging for source text.
   - Decisions: [D-012](knowledge-engine-decisions.md#d-012-scope-enforcement-is-private-by-default), [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time)
   - Exit criteria: credential-looking strings do not appear in derived artifacts; suspicious instructions are tagged/quarantined.
   - Required tests: redaction fixtures; injection fixtures; privacy scan.

3. Build the KE SQLite/FTS5 schema and rebuild command.
   - Decisions: [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace), [D-005](knowledge-engine-decisions.md#d-005-sqlite-fts5-is-the-local-first-knowledge-engine-store)
   - Exit criteria: index metadata pins event count, last event hash, state hash, schema version, and source authority.
   - Required tests: index rebuild determinism; index freshness; delete-and-rebuild.

4. Implement severity-based malformed artifact handling.
   - Decisions: [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory), [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time)
   - Exit criteria: non-critical malformed sources are reported/quarantined; ledger integrity failures fail closed.
   - Required tests: malformed non-critical fixture; ledger tamper fixture.

## Milestone M3: Evidence-Locked Entity Graph

Purpose: add relationship-aware memory without hallucinated graph facts.

Exit criteria:
- V1 entity and relationship schema is implemented.
- No active node/edge lacks citation and evidence hash.
- Low-confidence proposals are quarantined and excluded from normal answers.
- Graph rebuild is deterministic from sources plus promotion records.

Risks: [R-002](knowledge-engine-risks.md#r-002-hallucinated-graph-facts), [R-010](knowledge-engine-risks.md#r-010-stale-or-contradictory-memory-is-presented-as-settled-truth)

### Steps

1. Implement graph tables and provenance model in the KE store.
   - Decisions: [D-005](knowledge-engine-decisions.md#d-005-sqlite-fts5-is-the-local-first-knowledge-engine-store), [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked), [D-010](knowledge-engine-decisions.md#d-010-v1-graph-ontology-is-deliberately-small)
   - Exit criteria: entities, edges, citations, status, confidence, and evidence hashes are stored.
   - Required tests: schema migration/rebuild test; no-uncited-edge test.

2. Add deterministic extraction for obvious entities and relationships.
   - Decisions: [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked), [D-010](knowledge-engine-decisions.md#d-010-v1-graph-ontology-is-deliberately-small)
   - Exit criteria: file/repo/session/task/decision cues produce cited candidates where evidence exists.
   - Required tests: deterministic extraction fixtures.

3. Add model-assisted proposal path with quarantine.
   - Decisions: [D-007](knowledge-engine-decisions.md#d-007-reranking-is-pluggable-provider-agnostic-and-local-first), [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked), [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default), [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time)
   - Exit criteria: unsupported proposals are quarantined; provider calls are redacted/scope-logged.
   - Required tests: provider disabled test; provider redaction test; quarantine test.

4. Add graph validation and inspection commands.
   - Decisions: [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked), [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge)
   - Exit criteria: `knowledge graph status` reports active/quarantine counts and provenance health.
   - Required tests: CLI graph status/inspect tests.

## Milestone M4: Query Planner, Graph Expansion, Reranking, And Confidence

Purpose: make interactive recall behave like a knowledge engine.

Exit criteria:
- Query planner merges FTS, graph expansion, optional embeddings, and reranking.
- Default output is a concise answer with citations.
- `fast`, `normal`, `strict`, and `explore` modes behave differently and predictably.
- Scope filtering, stale facts, and contradictions are handled by policy.

Risks: [R-001](knowledge-engine-risks.md#r-001-cross-scope-leakage), [R-003](knowledge-engine-risks.md#r-003-provider-data-exposure), [R-005](knowledge-engine-risks.md#r-005-latency-misses-interactive-targets), [R-010](knowledge-engine-risks.md#r-010-stale-or-contradictory-memory-is-presented-as-settled-truth)

### Steps

1. Implement candidate generation from KE FTS and graph expansion.
   - Decisions: [D-003](knowledge-engine-decisions.md#d-003-source-policy-is-spine-first), [D-005](knowledge-engine-decisions.md#d-005-sqlite-fts5-is-the-local-first-knowledge-engine-store), [D-010](knowledge-engine-decisions.md#d-010-v1-graph-ontology-is-deliberately-small)
   - Exit criteria: query returns evidence candidates with citations and graph context.
   - Required tests: candidate retrieval fixtures.

2. Add optional embedding candidate generation.
   - Decisions: [D-006](knowledge-engine-decisions.md#d-006-embeddings-are-optional-and-advisory), [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default)
   - Exit criteria: query works when embeddings are disabled; embeddings never create facts.
   - Required tests: embeddings-disabled test; advisory-only test.

3. Add provider-pluggable reranking with graceful degradation.
   - Decisions: [D-007](knowledge-engine-decisions.md#d-007-reranking-is-pluggable-provider-agnostic-and-local-first), [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default), [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time)
   - Exit criteria: local-first path works; external provider private-content calls require explicit authorization; provider unavailable degrades to FTS + graph.
   - Required tests: local rerank fixture; provider-unavailable fixture; private-provider-authorization fixture.

4. Implement confidence modes and stale/conflict handling.
   - Decisions: [D-014](knowledge-engine-decisions.md#d-014-conflict-and-stale-fact-handling-is-intent-aware), [D-015](knowledge-engine-decisions.md#d-015-confidence-modes-gate-answer-behavior)
   - Exit criteria: strict mode refuses insufficient evidence; explore mode labels weak leads; current-state queries cite conflicts when present.
   - Required tests: seeded contradiction; superseded fact; strict refusal; explore weak lead.

5. Implement `knowledge query` outputs and latency metrics.
   - Decisions: [D-008](knowledge-engine-decisions.md#d-008-default-query-output-is-a-synthesized-answer-with-citations), [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge), [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)
   - Exit criteria: JSON/Markdown/text formats work; latency targets are measured in reports.
   - Required tests: CLI query format tests; latency smoke test.

## Milestone M5: Nightly Synthesis And Owner Promotion

Purpose: add "dream cycle" style derived intelligence without corrupting memory.

Exit criteria:
- Synthesis creates five artifacts through staging and atomic publish.
- Failed runs keep last successful artifacts active.
- Promotion is owner-only and writes normal ledger events.

Risks: [R-006](knowledge-engine-risks.md#r-006-synthesis-corrupts-durable-memory), [R-009](knowledge-engine-risks.md#r-009-prompt-injection-through-memory-content), [R-010](knowledge-engine-risks.md#r-010-stale-or-contradictory-memory-is-presented-as-settled-truth)

### Steps

1. Implement synthesis staging and artifact writers.
   - Decisions: [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace), [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional)
   - Exit criteria: daily brief, entity summaries, decision/timeline summaries, contradiction report, and open-question list are written only after validation.
   - Required tests: synthesis success fixture; artifact schema test.

2. Implement validation and atomic publish.
   - Decisions: [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional), [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time)
   - Exit criteria: uncited synthesis claims fail validation; failed run writes report and preserves last success.
   - Required tests: failed-run fixture; uncited-claim fixture.

3. Implement owner-only promotion path.
   - Decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional)
   - Exit criteria: promotion creates ledger event; derived artifact alone is not authority.
   - Required tests: promotion event test; non-owner/autonomous promotion blocked.

4. Add scheduler-agnostic runbook examples.
   - Decisions: [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional)
   - Exit criteria: docs show command contract and cron/launchd examples without making scheduler mandatory.
   - Required tests: documentation link check.

## Milestone M6: Hermes Skills And Multi-Workspace Federation

Purpose: make Hermes-Agent the priority proving ground while keeping the plugin reusable.

Exit criteria:
- Hermes provider exposes KE tools.
- Skills repo teaches agents when and how to use KE.
- Multiple workspaces can be queried through explicit federation with workspace-separated output.

Risks: [R-001](knowledge-engine-risks.md#r-001-cross-scope-leakage), [R-008](knowledge-engine-risks.md#r-008-multi-workspace-blending), [R-011](knowledge-engine-risks.md#r-011-dependency-bloat-undermines-local-first-install)

### Steps

1. Add Hermes provider tool schemas and handlers for KE query/status/synthesis status.
   - Decisions: [D-001](knowledge-engine-decisions.md#d-001-knowledge-engine-is-an-internal-total-recall-module), [D-008](knowledge-engine-decisions.md#d-008-default-query-output-is-a-synthesized-answer-with-citations), [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge)
   - Exit criteria: existing Hermes tests pass; new KE tool tests pass.
   - Required tests: Hermes provider lifecycle + KE tool tests.

2. Add skill instructions that call the stable CLI/API and cite memory-dependent claims.
   - Decisions: [D-001](knowledge-engine-decisions.md#d-001-knowledge-engine-is-an-internal-total-recall-module), [D-008](knowledge-engine-decisions.md#d-008-default-query-output-is-a-synthesized-answer-with-citations), [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge)
   - Exit criteria: skill docs do not import internals; instructions say use KE when memory/history/continuity is relevant.
   - Required tests: manual skill review; link check.

3. Implement multi-workspace index registry and explicit federation.
   - Decisions: [D-012](knowledge-engine-decisions.md#d-012-scope-enforcement-is-private-by-default), [D-016](knowledge-engine-decisions.md#d-016-multi-workspace-support-is-read-first-with-explicit-federation)
   - Exit criteria: workspace-separated output by default; cross-workspace synthesis requires explicit authorization.
   - Required tests: two-workspace fixture; federation authorization; no silent merge.

## Milestone M7: Evaluation, Scorecards, Documentation, And Release Gate

Purpose: prove the system improved search/intelligence without regressing Total Recall's trust spine.

Exit criteria:
- Synthetic fixtures and redacted Hermes smoke tests pass.
- Before/after scorecard exists.
- No KE layer below 7/10.
- No existing core gate regresses.
- README/runbook/architecture/scorecard/skill docs are updated.

Risks: [R-004](knowledge-engine-risks.md#r-004-scorecard-becomes-vanity-rather-than-proof), [R-005](knowledge-engine-risks.md#r-005-latency-misses-interactive-targets), [R-011](knowledge-engine-risks.md#r-011-dependency-bloat-undermines-local-first-install)

### Steps

1. Implement synthetic evaluation fixtures.
   - Decisions: [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked), [D-012](knowledge-engine-decisions.md#d-012-scope-enforcement-is-private-by-default), [D-014](knowledge-engine-decisions.md#d-014-conflict-and-stale-fact-handling-is-intent-aware), [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time), [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)
   - Exit criteria: fixtures cover retrieval, reranking, graph provenance, contradictions, scope leaks, tamper/forgery, synthesis derivation, and rebuild equivalence.
   - Required tests: evaluation fixture suite.

2. Implement redacted Hermes real-memory smoke tests.
   - Decisions: [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default), [D-016](knowledge-engine-decisions.md#d-016-multi-workspace-support-is-read-first-with-explicit-federation), [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)
   - Exit criteria: smoke reports redact private content and still prove the target environment works.
   - Required tests: redacted smoke test; privacy scan.

3. Implement before/after scorecards.
   - Decisions: [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)
   - Exit criteria: scorecard includes Knowledge Engine score and Trust Spine score, with evidence links.
   - Required tests: scorecard generation test.

4. Update public/operator docs.
   - Decisions: [D-001](knowledge-engine-decisions.md#d-001-knowledge-engine-is-an-internal-total-recall-module), [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge), [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)
   - Exit criteria: README, runbook, skill instructions, scorecard docs, and architecture diagram describe actual behavior after implementation.
   - Required tests: privacy scan; doc link check.

5. Run release gate.
   - Decisions: [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)
   - Exit criteria: stable release blocked unless all gates pass.
   - Required tests: `python scripts/privacy_scan.py`, `python -m pytest -q`, `scripts/install_smoke.sh`, `total-recall knowledge evaluate run`, `total-recall knowledge evaluate scorecard`.

## Cross-Link Map

| Roadmap area | Decisions | Primary risks |
|---|---|---|
| M1 shell/store/CLI | D-001, D-004, D-017, D-018, D-020 | R-007, R-011, R-012 |
| M2 source/index/sanitizer | D-002, D-003, D-004, D-005, D-012, D-017, D-019 | R-001, R-003, R-009 |
| M3 graph | D-005, D-007, D-009, D-010, D-013, D-018, D-019 | R-002, R-010 |
| M4 query/rerank/confidence | D-003, D-005, D-006, D-007, D-008, D-010, D-013, D-014, D-015, D-018, D-020 | R-001, R-003, R-005, R-010 |
| M5 synthesis/promotion | D-002, D-004, D-011, D-019 | R-006, R-009, R-010 |
| M6 Hermes/federation/skills | D-001, D-008, D-012, D-016, D-018 | R-001, R-008, R-011 |
| M7 evaluation/release/docs | D-001, D-009, D-012, D-013, D-014, D-016, D-018, D-019, D-020 | R-004, R-005, R-011 |

## Build-Readiness Walk

This walk verifies that every implementation step references existing decisions.

| Step | Decision references | Status |
|---|---|---|
| M0.1 Document current entry points and dependency graph | D-001, D-017 | Resolved |
| M0.2 Record load-bearing decisions and revisit triggers | D-001 through D-020 | Resolved |
| M0.3 Record risks with owners and monitoring signals | D-020 | Resolved |
| M1.1 Add internal module boundary | D-001, D-018 | Resolved |
| M1.2 Add `knowledge/` layout/status | D-004, D-017 | Resolved |
| M1.3 Add `knowledge status` and formats | D-018 | Resolved |
| M1.4 Update install smoke | D-017, D-020 | Resolved |
| M2.1 Implement source enumeration | D-002, D-003, D-017 | Resolved |
| M2.2 Implement redaction/injection tagging | D-012, D-019 | Resolved |
| M2.3 Build KE SQLite/FTS5 schema/rebuild | D-004, D-005 | Resolved |
| M2.4 Malformed artifact handling | D-017, D-019 | Resolved |
| M3.1 Graph tables/provenance | D-005, D-009, D-010 | Resolved |
| M3.2 Deterministic extraction | D-009, D-010 | Resolved |
| M3.3 Model-assisted proposals/quarantine | D-007, D-009, D-013, D-019 | Resolved |
| M3.4 Graph validation/inspection | D-009, D-018 | Resolved |
| M4.1 Candidate generation | D-003, D-005, D-010 | Resolved |
| M4.2 Optional embeddings | D-006, D-013 | Resolved |
| M4.3 Provider-pluggable reranking | D-007, D-013, D-019 | Resolved |
| M4.4 Confidence/stale/conflict handling | D-014, D-015 | Resolved |
| M4.5 Query outputs/latency metrics | D-008, D-018, D-020 | Resolved |
| M5.1 Synthesis staging/writers | D-004, D-011 | Resolved |
| M5.2 Validation/atomic publish | D-011, D-019 | Resolved |
| M5.3 Owner-only promotion | D-002, D-011 | Resolved |
| M5.4 Scheduler docs | D-011 | Resolved |
| M6.1 Hermes KE tools | D-001, D-008, D-018 | Resolved |
| M6.2 Skill instructions | D-001, D-008, D-018 | Resolved |
| M6.3 Multi-workspace federation | D-012, D-016 | Resolved |
| M7.1 Synthetic eval fixtures | D-009, D-012, D-014, D-019, D-020 | Resolved |
| M7.2 Redacted Hermes smoke tests | D-013, D-016, D-020 | Resolved |
| M7.3 Before/after scorecards | D-020 | Resolved |
| M7.4 Public/operator docs | D-001, D-018, D-020 | Resolved |
| M7.5 Release gate | D-020 | Resolved |

Build-readiness result: clean. No roadmap step references a missing decision.
