# Total Recall Knowledge Engine API And CLI Contract

- Owner: Alex Covo / Total Recall maintainer
- Last updated: 2026-05-18
- Stale by: 2026-06-18
- Status: build-ready planning
- Related docs: [roadmap](knowledge-engine-roadmap.md), [decisions](knowledge-engine-decisions.md), [architecture](knowledge-engine-architecture.md), [risks](knowledge-engine-risks.md), [tribal knowledge](knowledge-engine-tribal-knowledge.md), [runbook](knowledge-engine-runbook.md)

This document defines the external contract that implementation must preserve for agents, humans, and future harnesses.

## Version History

| Version | Status | Notes |
|---|---|---|
| 1.3.0 current working tree | implemented | Core ledger/checkpoint/anchor/search/rehydrate, derived LanceDB/QMD/SQLite retrieval, Hermes provider tools, and initial local-first Knowledge Engine CLI/API/provider tools. |
| 1.x Knowledge Engine V1 hardening | in progress | Deepens federation, graph inspection, provider adapters, synthetic/redacted-Hermes evaluations, and release gates without breaking current commands. |

## Compatibility Rules

- Existing commands remain valid.
- Existing stores remain readable unchanged.
- Existing `index/` derived caches remain non-authoritative.
- Knowledge Engine artifacts are derived under `$TOTAL_RECALL_HOME/knowledge/`.
- Any migration must be reversible and backed up.

Governing decisions: [D-001](knowledge-engine-decisions.md#d-001-knowledge-engine-is-an-internal-total-recall-module), [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory), [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge)

## Current CLI Surface

Current commands include:

```text
total-recall health
total-recall status
total-recall doctor
total-recall ingest
total-recall documents ingest
total-recall sources ingest
total-recall vault export
total-recall vault import-preview
total-recall vault import-promote
total-recall obsidian export
total-recall obsidian import-preview
total-recall obsidian import-promote
total-recall search
total-recall grep
total-recall checkpoint
total-recall verify
total-recall rehydrate
total-recall rehydrate-status
total-recall context
total-recall index status
total-recall index rebuild
total-recall index search
total-recall incidents ...
total-recall external ...
total-recall export
total-recall import
total-recall backup ...
total-recall dashboard
total-recall knowledge status
total-recall knowledge query
total-recall knowledge index status|rebuild
total-recall knowledge graph status|rebuild|inspect|traverse|timeline
total-recall knowledge freshness
total-recall knowledge truth status|build|show
total-recall knowledge synthesize status|run|promote
total-recall knowledge evaluate run|scorecard
total-recall federation register|list|remove|query
```

## Knowledge Engine CLI Surface

### `total-recall knowledge query`

Purpose: answer a memory/history question with citations, ranked evidence, and graph context.

Inputs:

```text
--query <text>
--mode fast|normal|strict|explore
--session-id <id>
--max-results <n>
--at-time <iso-timestamp>
--scope <scope> repeated
--federate <home-or-workspace> repeated
--authorize-federation
--external-provider <name> repeated
--authorize-external-provider
--format json|md|text
```

Default behavior:
- Default mode: `normal`.
- Agent-facing default format: `json`.
- Human direct answer can use `--format text`.
- Private scope is included only through the configured/default allowed scopes or explicit `--scope` filters.
- Federation is inert unless `--authorize-federation` is present; authorized federation returns workspace-separated evidence and does not silently merge answers.
- Local hash rerank always remains the first provider path. External providers are optional, skipped unless `--authorize-external-provider` is present, and currently degrade as `UNAVAILABLE` unless a future adapter is configured.
- Provider payload reports are written under `$TOTAL_RECALL_HOME/knowledge/providers/` and record provider family, locality, scopes sent, redaction counts, authorization, latency, success/failure, and federation status without raw memory text.

Minimum JSON fields:

```json
{
  "ok": true,
  "status": "PASS",
  "mode": "normal",
  "query": "...",
  "answer": "...",
  "confidence": {
    "level": "high",
    "score": 0.0,
    "reasons": []
  },
  "citations": [],
  "evidence": [],
  "graph": {
    "entities": [],
    "edges": []
  },
  "freshness": {
    "status": "PASS",
    "asOf": "...",
    "counts": {},
    "items": []
  },
  "scopeFilter": {
    "allowedScopes": [],
    "filtered": true
  },
  "providerCalls": [],
  "providerReport": null,
  "federation": {
    "requested": [],
    "authorized": false,
    "merged": false,
    "workspaces": [],
    "status": "NOT_REQUESTED"
  },
  "warnings": []
}
```

Citation minimum:

```json
{
  "event_id": "evt_...",
  "source_ref": "ledger:evt_...",
  "source_path": "ledger/events.jsonl",
  "timestamp": "...",
  "scope": "private",
  "session_id": "default",
  "trace_ref": null,
  "evidence_hash": "sha256..."
}
```

Governing decisions: [D-008](knowledge-engine-decisions.md#d-008-default-query-output-is-a-synthesized-answer-with-citations), [D-012](knowledge-engine-decisions.md#d-012-scope-enforcement-is-private-by-default), [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default), [D-014](knowledge-engine-decisions.md#d-014-conflict-and-stale-fact-handling-is-intent-aware), [D-015](knowledge-engine-decisions.md#d-015-confidence-modes-gate-answer-behavior)

### `total-recall sources ingest`

Purpose: ingest company working context that is not naturally a folder of files.

Inputs:

```text
--type agent_transcript|calendar|crm|email|github|meeting|slack|ticket
--text <body>
--file <path>
--title <title>
--actor <name>
--occurred-at <iso-timestamp>
--participant <name> repeated
--session-id <id>
--scope <scope>
--dry-run
--format json|text
```

Required behavior:
- Writes normal ledger events with kind `source_<type>`.
- Stores source metadata including source type, title, actor, participants, and
  `occurred_at` when provided.
- Uses `occurred_at` as the Knowledge Engine effective timestamp so imported
  sources can answer historical as-of questions.
- Dry-run previews the event without mutating the ledger.

### `total-recall knowledge freshness`

Purpose: classify cited memory as `current`, `stale`, `superseded`, or
`review_needed` for operational memory.

Inputs:

```text
--entity <subject>
--category promise|decision|customer|policy|project-state|task|memory
--at-time <iso-timestamp>
--scope <scope> repeated
--format json|text
```

Required behavior:
- Works from the rebuildable Knowledge Engine index.
- Uses effective timestamps when present.
- Detects explicit supersession metadata and newer same-subject memories for
  promises, decisions, customers, policies, and project state.
- Returns citations for every surfaced item.

### `total-recall knowledge index`

Purpose: build, rebuild, inspect, or validate Knowledge Engine derived stores.

Subcommands:

```text
total-recall knowledge index status
total-recall knowledge index rebuild
```

Required behavior:
- Rebuilds from authoritative source artifacts.
- Does not mutate ledger events.
- Emits JSON/Markdown reports.
- Quarantines malformed non-critical records and fails closed on ledger integrity failures.

Governing decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-003](knowledge-engine-decisions.md#d-003-source-policy-is-spine-first), [D-004](knowledge-engine-decisions.md#d-004-knowledge-engine-derived-artifacts-use-the-total-recall-home-knowledge-namespace), [D-005](knowledge-engine-decisions.md#d-005-sqlite-fts5-is-the-local-first-knowledge-engine-store), [D-017](knowledge-engine-decisions.md#d-017-backward-compatibility-is-mandatory), [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time)

### `total-recall knowledge graph`

Purpose: inspect and rebuild graph entities/edges.

Subcommands:

```text
total-recall knowledge graph status
total-recall knowledge graph rebuild
total-recall knowledge graph inspect --entity <name>
total-recall knowledge graph inspect --source-ref <ledger:evt_...>
total-recall knowledge graph traverse --entity <name> --depth <n>
total-recall knowledge graph timeline --entity <name> --at-time <iso-timestamp>
```

Required behavior:
- No active graph node or edge without citation.
- Inspect/traverse returns cited source evidence, not uncited graph assertions.
- Timeline separates `asOf` evidence from `afterAsOf` evidence when `--at-time`
  is supplied.
- Low-confidence proposals go to quarantine.
- Output supports JSON and Markdown.

Governing decisions: [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked), [D-010](knowledge-engine-decisions.md#d-010-v1-graph-ontology-is-deliberately-small)

### `total-recall knowledge truth`

Purpose: build and show the human-readable compiled-truth projection.

Subcommands:

```text
total-recall knowledge truth status
total-recall knowledge truth build
total-recall knowledge truth show --format json|md|text
```

Required behavior:
- Derived from the Knowledge Engine index and authoritative ledger events.
- Writes `knowledge/compiled/truth.json` and `knowledge/compiled/truth.md`.
- Groups cited decisions, promises, tasks, entity highlights, and timeline items.
- Carries projection hash, index state hash, source refs, and evidence hashes.
- Does not replace ledger/checkpoint/anchor authority.

Governing decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-008](knowledge-engine-decisions.md#d-008-default-query-output-is-a-synthesized-answer-with-citations), [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked)

### `total-recall vault export` And Import Review

Purpose: export a local Obsidian-compatible vault as a derived reading and
graph layer over the Total Recall ledger, then selectively promote edited notes
back through an explicit review loop.

Equivalent alias:

```text
total-recall obsidian export
total-recall obsidian import-preview
total-recall obsidian import-promote
```

Inputs:

```text
--out <folder>
--force
--scope <scope> repeated
--max-events <n>
--max-entities <n>
--format json|text
```

Required behavior:
- Refuses to write into a non-empty folder unless `--force` is present.
- Writes `Index.md`, `Compiled Truth.md`, `Graph Legend.md`, `README.md`,
  `.total-recall-vault.json`, and linked folders for sources, entities,
  documents, timeline, decisions, promises, and tasks.
- Uses Obsidian wikilinks while preserving `ledger:` source refs and evidence
  hashes.
- Remains a derived projection. Edits are imported only through
  `import-preview`, which writes review artifacts without changing the ledger,
  and `import-promote`, which writes normal `obsidian_note_import` ledger events.

Governing decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-009](knowledge-engine-decisions.md#d-009-entity-graph-is-evidence-locked)

### `total-recall federation`

Purpose: manage named cross-agent/workspace memory targets without silent global
memory merging.

Subcommands:

```text
total-recall federation register <name> <path> --role <role> --scope <scope>
total-recall federation list
total-recall federation remove <name>
total-recall federation query --query <text> --target <name> --authorize
```

Required behavior:
- Registered targets live under `$TOTAL_RECALL_HOME/federation/targets.json`.
- Querying another target requires `--authorize`.
- Authorized results remain workspace-separated under the `federation`
  response object.
- The current workspace is never silently merged with another workspace.

### `total-recall knowledge synthesize`

Purpose: run scheduler-agnostic nightly synthesis.

Subcommands:

```text
total-recall knowledge synthesize run
total-recall knowledge synthesize status
total-recall knowledge synthesize promote <proposal-id>
```

Required behavior:
- Writes to staging first.
- Validates citations/provenance.
- Atomically publishes complete artifacts.
- Keeps last successful artifacts active on failure.
- Promotion is owner-only and creates canonical ledger events through the existing append path.

Governing decisions: [D-002](knowledge-engine-decisions.md#d-002-the-deterministic-ledger-remains-the-authority), [D-011](knowledge-engine-decisions.md#d-011-nightly-synthesis-is-derived-and-provisional)

### `total-recall knowledge evaluate`

Purpose: prove release readiness and scorecard claims.

Subcommands:

```text
total-recall knowledge evaluate run
total-recall knowledge evaluate scorecard
```

Required behavior:
- Runs the current-store checks plus isolated synthetic fixtures for scope leaks, supersession/contradiction warnings, temporal as-of recall, report context fencing, provider report redaction, external-provider authorization/degradation, redacted Hermes-style smoke recall, and explicit workspace federation.
- Produces a Knowledge Engine scorecard and fixture count.
- Fails stable release if any required KE layer is below 7/10 or any core gate regresses.

Governing decision: [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)

### `total-recall trust`

Purpose: prove day-one/release readiness with hard-coded runtime checks rather
than docs, skills, or agent interpretation.

Subcommands:

```text
total-recall trust verify
total-recall trust status
```

Required behavior:
- Requires the real store ledger to hash-chain and the latest checkpoint/anchor
  to pin the current ledger state.
- Rebuilds/verifies derived retrieval and Knowledge Engine indexes from ledger
  authority.
- Proves real-store export/import persistence.
- Runs isolated synthetic fixtures for source ingest, freshness supersession,
  temporal graph timeline, Obsidian preview/promote, explicit federation, and
  fixture checkpoint/export/import restore.
- Verifies the generated, repo, and distributable Hermes plugin bundle exposes
  the required tool surface.
- Persists a JSON/Markdown trust-gate report and fails closed on any required
  gate failure.

## Hermes Provider Tools

Existing tools remain:

```text
total_recall_search
total_recall_status
total_recall_checkpoint
total_recall_verify
total_recall_trust_verify
total_recall_rehydrate
total_recall_incidents
total_recall_source_ingest
```

Knowledge Engine tools:

```text
total_recall_knowledge_query
total_recall_knowledge_freshness
total_recall_knowledge_status
total_recall_knowledge_synthesis_status
total_recall_knowledge_compiled_truth
total_recall_knowledge_graph_inspect
total_recall_knowledge_graph_timeline
total_recall_federation_query
```

Tool behavior:
- Use KE automatically when memory/history/continuity is relevant.
- Cite user-facing memory-dependent claims.
- Internal orientation use can be logged without visible citations.
- Strict mode refuses insufficient evidence.
- Explore mode can show weak leads with labels.

Governing decisions: [D-008](knowledge-engine-decisions.md#d-008-default-query-output-is-a-synthesized-answer-with-citations), [D-015](knowledge-engine-decisions.md#d-015-confidence-modes-gate-answer-behavior), [D-018](knowledge-engine-decisions.md#d-018-cli-namespace-is-total-recall-knowledge)

## Local SLA Targets

These are local UX targets, not hosted-service promises.

| Action | Target |
|---|---:|
| Current-state lookup | Under 1 second |
| Normal cited KE answer | Under 3 seconds |
| Deep graph/timeline/contradiction inspection | Under 10 seconds |
| Nightly synthesis | Batch only; no interactive target |

Governing decisions: [D-005](knowledge-engine-decisions.md#d-005-sqlite-fts5-is-the-local-first-knowledge-engine-store), [D-007](knowledge-engine-decisions.md#d-007-reranking-is-pluggable-provider-agnostic-and-local-first), [D-020](knowledge-engine-decisions.md#d-020-evaluation-gates-stable-release)

## Deprecation Policy

- Do not deprecate `search`, `context`, or `rehydrate` for V1.
- `knowledge query` can become the preferred intelligent recall path.
- Any future deprecation must first add a warning period and docs migration note.

## Provider Boundary Contract

Provider calls must record:
- provider family
- local vs external
- scopes sent
- redaction count
- authorization source for private external calls
- latency
- success/failure

Provider calls must not record raw private content or secrets in logs/reports.

Governing decisions: [D-007](knowledge-engine-decisions.md#d-007-reranking-is-pluggable-provider-agnostic-and-local-first), [D-013](knowledge-engine-decisions.md#d-013-private-memory-stays-local-by-default), [D-019](knowledge-engine-decisions.md#d-019-abuse-defenses-run-at-indexing-query-and-synthesis-time)
