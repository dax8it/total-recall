# Total Recall Knowledge Engine Risks

- Owner: Alex Covo / Total Recall maintainer
- Last updated: 2026-05-18
- Stale by: 2026-06-18
- Status: build-ready planning
- Related docs: [roadmap](knowledge-engine-roadmap.md), [decisions](knowledge-engine-decisions.md), [architecture](knowledge-engine-architecture.md), [API](knowledge-engine-api.md), [tribal knowledge](knowledge-engine-tribal-knowledge.md), [runbook](knowledge-engine-runbook.md)

Each risk has an owner and a monitoring signal. Owners are intentionally simple because this is a solo/local-first project.

## R-001: Cross-scope leakage

- Owner: Total Recall maintainer
- Blast radius: private memories could appear in shared, group, federated, or provider-bound output.
- Decisions: [D-012](knowledge-engine-decisions.md#d-012-scope-enforcement-is-private-by-default), [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default), [D-016](knowledge-engine-decisions.md#d-016-multi-workspace-support-is-read-first-with-explicit-federation)
- Mitigation: per-record scope on source and derived records; private-by-default; explicit federation authorization; provider scope logging; no disclosure that hidden private evidence exists.
- Monitoring signal: eval leak tests fail, provider-call report contains unexpected scope, or query output cites a disallowed scope.

## R-002: Hallucinated graph facts

- Owner: Total Recall maintainer
- Blast radius: graph edges could cause false answers, bad synthesis, or misleading scorecard gains.
- Decisions: [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked), [D-010](knowledge-engine-decisions.md#d-010-v1-graph-ontology-is-deliberately-small), [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)
- Mitigation: no uncited nodes or edges; evidence hashes; quarantine weak proposals; graph rebuild equivalence tests.
- Monitoring signal: graph validation report shows uncited edges, quarantine rate spikes, or eval finds unsupported graph claims.

## R-003: Provider data exposure

- Owner: Total Recall maintainer
- Blast radius: private memory or secrets could be sent to external providers.
- Decisions: [D-007](knowledge-engine-decisions.md#d-007-reranking-is-pluggable-provider-agnostic-and-local-first), [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default), [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time)
- Mitigation: local-first defaults; explicit owner authorization for private external calls; redaction before provider payloads; provider-call reports log scopes and redaction counts.
- Monitoring signal: provider-call report includes private scope without authorization or redaction report finds unredacted credential-like content.

## R-004: Scorecard becomes vanity rather than proof

- Owner: Total Recall maintainer
- Blast radius: release could claim gbrain-style improvements without measurable behavior.
- Decisions: [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release), [D-015](knowledge-engine-decisions.md#d-015-confidence-modes-gate-answer-behavior)
- Mitigation: scorecard backed by synthetic fixtures and redacted Hermes smoke tests; before/after evidence stored in reports; stable release blocked below threshold.
- Monitoring signal: scorecard item lacks linked eval receipt or has manual-only evidence.

## R-005: Latency misses interactive targets

- Owner: Total Recall maintainer
- Blast radius: Hermes-Agent becomes slower or avoids using Knowledge Engine.
- Decisions: [D-005](knowledge-engine-decisions.md#d-005-sqlite-fts5-is-the-local-first-knowledge-engine-store), [D-006](knowledge-engine-decisions.md#d-006-embeddings-are-optional-and-advisory), [D-007](knowledge-engine-decisions.md#d-007-reranking-is-pluggable-provider-agnostic-and-local-first)
- Mitigation: 100K-event load tests; query-mode budgets; degrade to FTS + graph when providers are unavailable or slow; keep nightly synthesis out of interactive path.
- Monitoring signal: eval latency report exceeds 1s current lookup, 3s normal query, or 10s deep inspection thresholds.

## R-006: Synthesis corrupts durable memory

- Owner: Total Recall maintainer
- Blast radius: provisional summaries could become false canonical memory.
- Decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional)
- Mitigation: derived/provisional synthesis only; owner-only promotion; atomic publish from staging; failed-run report keeps last successful artifacts active.
- Monitoring signal: synthesis writes directly to ledger without owner promotion, or validation finds uncited synthesis claims.

## R-007: Backward compatibility or history loss

- Owner: Total Recall maintainer
- Blast radius: existing Hermes-Agent history becomes unreadable or migration damages the ledger.
- Decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory)
- Mitigation: sidecar-first KE storage; no event-file mutation; backup before migrations; reversible migration receipts; read-current-artifacts tests.
- Monitoring signal: import/verify/search tests fail on a pre-KE fixture or migration receipt lacks backup pointer.

## R-008: Multi-workspace blending

- Owner: Total Recall maintainer
- Blast radius: one agent workspace could receive another workspace's context without explicit request.
- Decisions: [D-016](knowledge-engine-decisions.md#d-016-multi-workspace-support-is-read-first-with-explicit-federation), [D-012](knowledge-engine-decisions.md#d-012-scope-enforcement-is-private-by-default)
- Mitigation: per-workspace indexes; workspace-separated federated output; explicit authorization for cross-workspace synthesis.
- Monitoring signal: federation eval returns merged answer without requested synthesis or cites a workspace not in authorization.

## R-009: Prompt injection through memory content

- Owner: Total Recall maintainer
- Blast radius: malicious text in events or transcript snippets could steer retrieval, provider prompts, or synthesis.
- Decisions: [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time)
- Mitigation: treat retrieved memory as evidence only; strip/ignore instructions embedded in memory; quarantine suspicious content; synthesis ignores source instructions.
- Monitoring signal: injection fixture causes tool/action instruction to appear as agent instruction instead of quoted evidence.

## R-010: Stale or contradictory memory is presented as settled truth

- Owner: Total Recall maintainer
- Blast radius: agent makes wrong decisions from superseded facts.
- Decisions: [D-014](knowledge-engine-decisions.md#d-014-conflict-and-stale-fact-handling-is-intent-aware), [D-015](knowledge-engine-decisions.md#d-015-confidence-modes-gate-answer-behavior)
- Mitigation: supersedes/contradicts graph edges; contradiction report; query intent detection; strict mode refuses low-confidence claims.
- Monitoring signal: seeded contradiction eval returns a settled answer without conflict citation.

## R-011: Dependency bloat undermines local-first install

- Owner: Total Recall maintainer
- Blast radius: install smoke slows down or fails for users who only need core continuity.
- Decisions: [D-001](knowledge-engine-decisions.md#d-001-knowledge-engine-is-an-internal-total-recall-module), [D-005](knowledge-engine-decisions.md#d-005-sqlite-fts5-is-the-local-first-knowledge-engine-store), [D-006](knowledge-engine-decisions.md#d-006-embeddings-are-optional-and-advisory)
- Mitigation: optional extras for semantic providers; SQLite-only base path; install smoke covers base install.
- Monitoring signal: base install imports provider SDKs, install smoke requires optional packages, or package size jumps without a reason.

## R-012: Current-store path drift

- Owner: Total Recall maintainer
- Blast radius: implementation writes KE data under the wrong root or breaks export/import assumptions.
- Decisions: [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory)
- Mitigation: explicit store-layout tests; export/import policy for `knowledge/`; architecture doc flags the drift from the earlier `memory/knowledge/` wording.
- Monitoring signal: generated artifacts appear outside `$TOTAL_RECALL_HOME/knowledge/` or privacy scan catches local absolute paths in docs/reports.
