# Total Recall Knowledge Engine Decisions

- Owner: Alex Covo / Total Recall maintainer
- Last updated: 2026-05-18
- Stale by: 2026-06-18
- Status: build-ready planning
- Related docs: [roadmap](knowledge-engine-roadmap.md), [architecture](knowledge-engine-architecture.md), [risks](knowledge-engine-risks.md), [API](knowledge-engine-api.md), [tribal knowledge](knowledge-engine-tribal-knowledge.md), [runbook](knowledge-engine-runbook.md)

This document records the decisions required before implementing the Knowledge Engine module inside Total Recall. The decisions are intentionally sized for a solo/local-first project: enough structure to prevent drift, not a committee process.

## D-001: Knowledge Engine is an internal Total Recall module

Decision: Total Recall remains one reusable plugin/system. Knowledge Engine is an internal module exposed through the existing Total Recall package and CLI, not a second plugin.

Alternatives considered:
- Separate `Total Recall KE` plugin.
- Fold all behavior into the existing `search` command with no module boundary.

Reasoning: One plugin keeps install and agent skill usage simple. An internal module gives the new graph, rerank, and synthesis code a clear boundary without splitting the product.

Revisit trigger: Revisit only if another agent harness needs to install Knowledge Engine without the ledger/checkpoint core.

## D-002: The deterministic ledger remains the authority

Decision: Existing ledger events, reduced state, checkpoints, and anchors remain authoritative. Knowledge Engine data is derived unless explicitly promoted through the existing append/promotion path.

Alternatives considered:
- Let graph/synthesis write directly into state.
- Let provider outputs become memory automatically.

Reasoning: Total Recall's strongest feature is verifiable continuity. The Knowledge Engine must improve search and intelligence without weakening proof, recovery, or auditability.

Revisit trigger: Revisit if a future trust split creates a separate steward process with its own signed append authority.

## D-003: Source policy is spine-first

Decision: V1 indexes ledger events, reduced current state, rollups when present, and Knowledge Engine synthesis outputs. Transcripts and handoffs are fallback/citation sources only. Generated reports, skill docs, and runtime logs are excluded unless promoted into ledger events.

Alternatives considered:
- Bulk-index every file.
- Index transcripts by default.

Reasoning: Bulk indexing would add bloat, leak risk, and semantic noise. The spine-first policy keeps the index sharp and rebuildable.

Revisit trigger: Revisit if evaluations prove transcript snippets materially improve answers without increasing leakage/noise.

## D-004: Knowledge Engine derived artifacts use the Total Recall home knowledge namespace

Decision: V1 uses `$TOTAL_RECALL_HOME/knowledge/` for derived Knowledge Engine artifacts. The earlier brief phrase `memory/knowledge/` is normalized to the current codebase convention: `$TOTAL_RECALL_HOME` is already the memory store root.

Alternatives considered:
- Literal `memory/knowledge/`.
- Existing `$TOTAL_RECALL_HOME/index/`.

Reasoning: The current repo layout has `ledger/`, `state/`, `checkpoints/`, `anchors/`, `reports/`, `incidents/`, `external-memory/`, `index/`, and `keys/` directly under `$TOTAL_RECALL_HOME`. A sibling `knowledge/` namespace keeps KE derived data separate from current retrieval caches and makes it disposable.

Revisit trigger: Revisit if the store layout is versioned to introduce a top-level `memory/` directory.

## D-005: SQLite FTS5 is the local-first Knowledge Engine store

Decision: Use SQLite + FTS5 as the local-first durable derived store for KE indexes, graph tables, citations, synthesis metadata, and evaluation receipts. Optional vector/HNSW/graph adapters can be added later behind provider boundaries.

Alternatives considered:
- Separate graph database in V1.
- Mandatory vector database/HNSW in V1.
- Only use the existing SQLite retrieval table.

Reasoning: SQLite is already in the project, easy to rebuild, and enough for 100K-event V1 tests. Separate graph/vector services add operational weight before the user experience proves it needs them.

Revisit trigger: Revisit if 100K-event tests miss the latency targets or if real Hermes memory shows graph traversal/query patterns that SQLite cannot support cleanly.

## D-006: Embeddings are optional and advisory

Decision: Embeddings may generate candidates in V1, but citations and ledger provenance determine final answer eligibility. FTS + graph expansion must work without embeddings.

Alternatives considered:
- No embeddings until V2.
- Embeddings as the primary source of truth.

Reasoning: The gbrain comparison expects semantic behavior, but Total Recall must not let semantic similarity create facts.

Revisit trigger: Revisit if the optional embedding path becomes required to pass the 7/10 Knowledge Engine score.

## D-007: Reranking is pluggable, provider-agnostic, and local-first

Decision: Reranking supports local/offline providers first, with OpenAI, Anthropic, Google/Gemini, Ollama/llama.cpp, and Hugging Face adapters allowed through a common provider contract. If no provider is available, the system degrades to FTS + graph.

Alternatives considered:
- One hosted reranker.
- Local-only forever.
- Fail all KE queries without reranking.

Reasoning: Provider choice should not lock the plugin to one model vendor, and ordinary recall should still work without semantic providers.

Revisit trigger: Revisit if provider abstraction makes the implementation too shallow or if a local default cannot meet quality targets.

## D-008: Default query output is a synthesized answer with citations

Decision: `total-recall knowledge query` returns a concise synthesized answer with citations by default. JSON output includes ranked evidence and graph context for agents and debugging.

Alternatives considered:
- Raw graph/evidence only.
- Always return a verbose report.

Reasoning: The user experience should feel like a knowledge engine, not a database browser, while preserving inspectability.

Revisit trigger: Revisit if Hermes-Agent performs better when handed raw evidence instead of synthesized text.

## D-009: Entity graph is evidence-locked

Decision: Graph extraction is hybrid: deterministic rules plus model-assisted proposals. No node or edge enters the active graph without a source citation and evidence hash. Weak proposals go to quarantine.

Alternatives considered:
- Deterministic extraction only.
- Let model extraction directly populate the graph.

Reasoning: Hallucinated memory is unacceptable. Hybrid extraction improves coverage while citations preserve trust.

Revisit trigger: Revisit if model-assisted extraction cannot be validated cheaply enough for nightly use.

## D-010: V1 graph ontology is deliberately small

Decision: V1 supports entity types `person`, `agent`, `project`, `repo`, `file`, `task`, `decision`, `concept`, `session`, and `organization`. `organization` is tag/link only in V1. Relationship types are `mentions`, `about`, `created_by`, `assigned_to`, `works_on`, `belongs_to_project`, `located_in_file`, `depends_on`, `decided`, `supersedes`, `contradicts`, `supports`, `caused_by`, `happened_in_session`, and `references`.

Alternatives considered:
- Bigger ontology.
- No fixed ontology.

Reasoning: A smaller action-oriented graph improves recall answers without turning V1 into ontology engineering.

Revisit trigger: Revisit after real Hermes-Agent smoke tests show repeated missing relationship classes.

## D-011: Nightly synthesis is derived and provisional

Decision: Nightly synthesis produces daily briefs, entity summaries, decision/timeline summaries, contradiction reports, and open-question lists. These artifacts are derived/provisional. Durable promotion is owner-only through the existing ledger append/promotion path.

Alternatives considered:
- Synthesis writes authoritative memory automatically.
- No synthesis until graph quality is perfect.

Reasoning: Overnight insight is useful, but derived layers must remain rebuildable and non-authoritative.

Revisit trigger: Revisit if owner promotion is too slow for real workflows and a stricter steward process exists.

## D-012: Scope enforcement is private-by-default

Decision: Every source record, derived record, graph item, evidence result, provider call, and synthesis artifact carries a scope. Missing scope means private. V1 scopes are `private`, `internal`, `group_safe`, and `public`. Existing `shared_team` must be treated as a legacy/compatibility scope until migrated or aliased.

Alternatives considered:
- Global trusted memory.
- Per-workspace only, no per-record scope.

Reasoning: Multi-agent and future team usage are too leak-prone without per-record scope. Current code already has `private`, `group_safe`, `internal`, and `shared_team`; the new model must preserve existing readable history.

Revisit trigger: Revisit when multi-user/org permissions become a V2 goal.

## D-013: Private memory stays local by default

Decision: External/API providers receive only redacted/minimized context unless the owner explicitly authorizes private content for that run. Local providers may receive private content only in allowed private/owner contexts. Provider calls log scopes sent, not raw secrets.

Alternatives considered:
- Let providers see all retrieved context.
- Disable external providers entirely.

Reasoning: This balances provider flexibility with privacy and local-first behavior.

Revisit trigger: Revisit if a formal encrypted provider relay or enterprise trust boundary is introduced.

## D-014: Conflict and stale-fact handling is intent-aware

Decision: Current-state queries prefer latest non-superseded high-confidence facts while citing conflicts. Timeline/investigative queries surface history. Safety/privacy/irreversible conflicts become unresolved open questions rather than settled claims.

Alternatives considered:
- Always hide old facts.
- Always show full history.
- Always choose newest.

Reasoning: Users need concise current truth most of the time, but memory must not bury contradictions when they matter.

Revisit trigger: Revisit if evaluation shows users miss important history in normal mode.

## D-015: Confidence modes gate answer behavior

Decision: V1 supports `fast`, `normal`, `strict`, and `explore`. High confidence is a weighted evidence gate: verified citation, allowed scope, non-superseded status, contradiction check, retrieval/rerank threshold, and graph consistency; multiple sources boost confidence.

Alternatives considered:
- One global threshold.
- Always answer with caveats.

Reasoning: Different agent tasks need different tradeoffs between speed, strictness, and exploration.

Revisit trigger: Revisit if the evaluation harness cannot make score differences explainable by mode.

## D-016: Multi-workspace support is read-first with explicit federation

Decision: V1 supports multiple agent workspaces with one KE index per workspace. Federation is explicit and authorized. Federated output is workspace-separated by default. Durable promotions/appends occur only in the targeted workspace unless an owner-authorized cross-workspace command is introduced later.

Alternatives considered:
- One global shared index.
- Automatic cross-workspace synthesis.

Reasoning: Workspace-local indexes preserve provenance and reduce leakage risk. Explicit federation gives the user cross-workspace power without accidental blending.

Revisit trigger: Revisit if multiple workspaces repeatedly need the same shared graph and scope model.

## D-017: Backward compatibility is mandatory

Decision: V1 must read current artifacts unchanged. Migrations must be non-destructive, backed up, reversible, and preserve old artifacts as source truth. Existing commands remain supported.

Alternatives considered:
- Break old store formats.
- Rewrite event files to add KE fields.

Reasoning: Existing history is important and must not be risked for a derived layer.

Revisit trigger: Revisit only for a major version with a tested migration/export/import path.

## D-018: CLI namespace is total-recall knowledge

Decision: Expose Knowledge Engine through a single CLI namespace with JSON/Markdown/text formats. Agent-facing defaults are JSON. Human reports default to Markdown. Query can emit text for direct chat injection.

Alternatives considered:
- Separate scripts.
- New binary.

Reasoning: One CLI namespace is easier for skills and agent harnesses and lets internals change without breaking integrations.

Revisit trigger: Revisit if another runtime needs a non-CLI API as the primary contract.

## D-019: Abuse defenses run at indexing, query, and synthesis time

Decision: Prompt-injection detection, secret redaction, scope checks, and malicious-content handling run before indexing, before provider calls, during query assembly, and during synthesis. Raw transcripts are not bulk-indexed; only redacted citation-tied snippets may be cached.

Alternatives considered:
- Query-time defense only.
- Trust ledger content as safe instructions.

Reasoning: Memory content is evidence, not instructions. Defenses must exist before content can influence graph/synthesis outputs.

Revisit trigger: Revisit if false positives quarantine too much useful memory.

## D-020: Evaluation gates stable release

Decision: Stable V1 is blocked if any required Knowledge Engine layer scores below 7/10 or any existing Total Recall core gate regresses. Experimental/beta artifacts may exist but cannot be called V1 complete.

Alternatives considered:
- Ship with known gaps.
- Manual scorecard only.

Reasoning: The goal is to improve the gbrain-style score without losing Total Recall's core strengths. Evaluation evidence keeps the claim honest.

Revisit trigger: Revisit only if the user changes the release definition from stable to experimental.
