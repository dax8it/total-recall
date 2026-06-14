# Total Recall Against The Agent Memory Stack

Date: 2026-06-03

Status: working-tree assessment after the Knowledge Engine, document ingest, source ingest, Obsidian/vault export/import review, freshness reporting, temporal graph timeline, Hermes tool expansion, and named federation implementation.

This article compares Total Recall with the current agent-memory landscape, especially GBrain after its recent updates. The scoring below is not a universal product benchmark. It scores each system for our intended Hermes stack: local-first continuity, cited recall, recoverable agent state, explicit trust boundaries, and memory that can survive restarts, compaction, tool churn, and operator handoff.

## Executive Take

Total Recall is no longer just a searchable notebook or rehydrate helper. It now occupies a distinct category: continuity authority plus evidence-locked knowledge engine.

That puts it beside systems like GBrain, Hindsight, Zep/Graphiti, Mem0, and Letta, but not in the same lane. GBrain is becoming the best open personal/company brain. Zep/Graphiti is the strongest temporal graph product layer. Hindsight is strongest as a structured retain/recall/reflect service. Mem0 is strongest as quick production memory. Letta is strongest when you want the whole agent runtime to own state.

Total Recall's bet is different: memory should be provable before it is useful.

The core advantage is not that Total Recall remembers more than everyone else today. It is that Total Recall can say why a memory is allowed to matter: ledger event, hash chain, checkpoint, signed anchor, citation, evidence hash, scope, provider receipt, and fail-closed verification.

## Scorecard

Scoring lens: fit for our Hermes stack, not vendor leaderboard performance.

| System | Score | Best At | Main Gap For Us |
|---|---:|---|---|
| Total Recall | 94/100 | Trust-rooted continuity, cited recall, compiled truth, Obsidian/vault export/import review, graph inspection/traversal/timeline, freshness reporting, fail-closed rehydrate, explicit federation | No remote OAuth MCP/admin surface yet, no turnkey SaaS connectors or rich typed business ontology |
| GBrain | 89/100 | Markdown brain repo, self-wiring graph, skills, MCP/CLI breadth, operator-owned knowledge | Less of a cryptographic continuity authority; correctness depends on brain hygiene and agent write discipline |
| Zep / Graphiti | 90/100 | Temporal graph memory, invalidating stale facts, low-latency ranked context at product scale | Product/platform orientation; not a local Hermes continuity ledger |
| Hindsight | 88/100 | Structured retain/recall/reflect, synthesis over memory banks, managed memory platform | External memory service posture; source of truth is not our signed local ledger |
| Mem0 | 84/100 | Fast setup, managed memory, hybrid/entity-linked retrieval, broad integrations | Recent OSS changes removed direct graph-store support; less suited as continuity authority |
| Letta | 81/100 | Stateful agent runtime, editable memory blocks, persisted messages/tools/runs | More runtime than drop-in Hermes MemoryProvider; less focused on cryptographic provenance |
| Native Hermes memory | 62/100 | Always-visible small notes and local session archive | Small, model-managed, limited semantic/graph/synthesis guarantees |

If we score only for remote MCP/admin polish, GBrain still has the more mature surface today. If we score for product-scale temporal graph serving, Zep still wins. If we score for the Hermes stack we actually need - continuity authority plus readable, cited, graph-aware recall - Total Recall now clears GBrain because the ledger/checkpoint/anchor model is the product and the new Knowledge Engine surfaces make that authority usable.

## Where Total Recall Lives

Total Recall lives in three places at once:

1. Framework-neutral core: `total_recall_core` owns the ledger, state reduction, checkpoints, anchors, verification, rehydrate, backups, external-memory queues, reports, and Knowledge Engine.
2. Hermes MemoryProvider plugin: `hermes-plugin/total-recall` adapts the core into Hermes lifecycle hooks and tools. It is a real memory provider, not a skill pretending to be a database.
3. Knowledge Engine internal module: `src/total_recall_core/knowledge.py` builds derived intelligent recall on top of the authoritative ledger.

That placement matters. GBrain is primarily a brain repo exposed through CLI/MCP and taught through skills. Total Recall is a MemoryProvider and continuity authority first; skills should teach Hermes when to call `total-recall knowledge ...`, not become memory storage themselves.

## What Total Recall Does Now

The current working tree now gives Hermes:

- Append-only event ledger with hash chaining.
- Deterministic state reduction.
- Signed checkpoints and anchors.
- Fail-closed verify and rehydrate.
- Retrieval ladder: LanceDB, QMD, SQLite/FTS, lexical fallback.
- Generated-report exclusion so recall output does not recursively become future memory input.
- Knowledge Engine CLI: `query`, `status`, `index`, `graph`, `synthesize`, `evaluate`.
- Hermes tools: `total_recall_knowledge_query`, `total_recall_knowledge_freshness`, `total_recall_knowledge_status`, `total_recall_knowledge_synthesis_status`, `total_recall_knowledge_compiled_truth`, `total_recall_knowledge_graph_inspect`, `total_recall_knowledge_graph_timeline`, `total_recall_source_ingest`, and `total_recall_federation_query`.
- Compiled truth projection: `knowledge truth build/show` writes ledger-derived markdown/JSON for decisions, promises, tasks, entity highlights, and timelines.
- Obsidian/vault export: `vault export` and `obsidian export` write wikilinked `Sources/`, `Entities/`, `Documents/`, `Timeline/`, `Decisions/`, `Promises/`, and `Tasks/` notes plus a manifest.
- Obsidian import review: edited notes go through `vault import-preview` and `vault import-promote`, with review artifacts before ledger writes.
- Working-context source ingest: `sources ingest` accepts meetings, email, Slack, GitHub, CRM, tickets, calendars, and agent transcripts.
- Freshness reporting: `knowledge freshness` classifies current/stale/superseded memory for promises, decisions, customers, policies, project state, and tasks.
- Evidence-locked graph: active entities and edges require source refs plus evidence hashes.
- Graph inspect/traverse/timeline: usable entity/context exploration that returns cited source evidence rather than uncited graph assertions, including as-of versus after-as-of splits.
- Multi-strategy Knowledge Engine query: FTS, lexical matching, graph expansion, local hash rerank, recency, temporal filter, contradiction/supersession warnings.
- Temporal query mode with `--at-time`, plus effective timestamps from source metadata such as `occurred_at`.
- Provisional synthesis with owner-only promotion back into the ledger.
- Provider payload reports under `knowledge/providers/` without raw private memory text.
- Explicit read-only workspace federation with `--federate` plus `--authorize-federation`, and a named `federation register/list/remove/query` registry; no silent global memory soup.
- Optional external-provider contract: skipped without explicit authorization, degraded as unavailable when authorized but unconfigured.
- Evaluation harness with scope-leak, contradiction, temporal, context-fencing, provider-report, external-provider, redacted-Hermes, and federation fixtures.

Current local validation is tracked through the project's test, benchmark, and trust-gate stack (see [benchmarks.md](benchmarks.md)).

## What Total Recall Does Better

### 1. Continuity Authority

Most memory tools optimize recall quality. Total Recall optimizes whether remembered context can be trusted after compression, restart, import/export, plugin churn, or corruption.

That is the ledger/checkpoint/anchor advantage. A retrieved memory is not just a vector hit. It is tied back to a source event and, for strict rehydrate paths, to verification state.

### 2. Fail-Closed Behavior

If the ledger or anchor is tampered with, Total Recall refuses trusted rehydrate. Many memory systems degrade by returning whatever their index says. Total Recall's default posture is closer to an aircraft checklist: no verified authority, no authoritative memory block.

### 3. Context Fencing

Total Recall explicitly excludes generated reports from retrieval. That is a big deal. Memory systems that auto-store their own recall output can create feedback loops where the agent starts remembering summaries of summaries as truth.

### 4. Explicit Federation

GBrain, PLUR-style folders, and shared memory systems are powerful because knowledge moves between agents. Total Recall borrows the portability idea but refuses silent merge. A federated query is local unless explicitly authorized, and authorized results stay workspace-separated.

### 5. Provider Accountability

The new provider reports record provider family, locality, scopes sent, redactions, authorization, status, latency, and federation posture. That is less flashy than a benchmark number, but it is what you want when memory influences high-consequence actions.

### 6. Hermes-Native Placement

Total Recall is in the Hermes MemoryProvider slot. It participates in lifecycle hooks like prefetch, sync turn, session switch, pre-compress, checkpoint, verify, and rehydrate. GBrain can be used by Hermes, but it is more naturally a brain/MCP/skill layer alongside the provider.

## What Total Recall Does Not Do Yet

### 1. It Is Still Not A Full Markdown Brain Repo

GBrain's design is built around markdown as the source of truth: compiled truth above the line, timeline below, typed links, graph traversal, page versions, skills, maintenance jobs, and human-readable diffs. Total Recall now has a compiled-truth markdown projection and an Obsidian-compatible vault export, but its source of truth remains the event ledger, not a curated markdown knowledge base.

That is a trade. Total Recall is now much closer to the Obsidian/GBrain reading experience, while staying more rigorous as Hermes continuity infrastructure. GBrain is still more pleasant as a broad living human/agent brain repo where markdown itself is the workspace.

### 2. It Does Not Yet Have GBrain's Remote MCP Surface

Current GBrain docs describe `gbrain serve --http` with OAuth 2.1, an admin dashboard, scoped operations, and live SSE activity. That is a serious operational update. Total Recall has a Hermes plugin and local CLI, but not that remote MCP/admin surface.

### 3. It Does Not Yet Have Zep/Graphiti's Temporal Graph Depth

Total Recall now has point-in-time query filtering, effective source timestamps, freshness reports, supersession warnings, and graph timeline splits. Zep/Graphiti still models temporal validity more deeply on graph edges: valid time, invalidation time, learned time, and retrieval over current or historical relationship facts. Total Recall should borrow typed validity intervals next.

### 4. It Does Not Yet Have Managed-Cloud Product Ergonomics

Mem0 and Hindsight are easier to sell to app teams that want memory in an afternoon. Total Recall is a local-first engine. That is correct for Hermes continuity, but it means fewer batteries for dashboards, multi-tenant API keys, hosted storage, and drop-in SaaS instrumentation.

### 5. It Does Not Yet Have Turnkey Ingestion Connectors

GBrain's operator story is meetings, emails, tweets, voice notes, markdown vaults, and daily maintenance. Total Recall now has a normalized local source-ingest command for meetings, email, Slack, GitHub, CRM, tickets, calendars, and agent transcripts, but it does not yet ship OAuth/API connectors, file watchers, or a daily-life ingestion pipeline.

### 6. The Graph Ontology Is Deliberately Small

This is good for safety but limiting for recall. The current graph identifies sessions, files, concepts, decisions, references, about, decided, and supersedes-style edges, and it now has inspect/traverse commands. We still need richer entities, typed relationships, quarantine browsing, entity resolution, and better graph-query ergonomics.

## GBrain Has Moved: What We Should Learn From It

GBrain is no longer just the launch idea of markdown plus Postgres. Current public docs show several important expansions:

- Markdown brain repo remains the source of truth.
- PGLite or Postgres backs retrieval.
- CLI and MCP expose contract-first operations.
- Skills are first-class workflows for ingest, query, maintain, enrich, briefing, and migration.
- Recent remote MCP docs describe HTTP serving with OAuth 2.1, scoped clients, admin dashboard, and SSE activity.
- Recent hardening defaults request logs and activity feeds to redacted parameter summaries.

The lesson for Total Recall is not to become GBrain. The lesson is to become better at the parts GBrain proves matter:

- Expand compiled-truth projections into page-versioned exports and selected-note import, but keep the ledger as authority.
- Expand graph traversal and inspection into richer entity pages, but keep evidence hashes mandatory.
- Add maintenance jobs, but make every promotion owner-authorized.
- Add remote/admin surfaces, but preserve local-first fail-closed mode.
- Add redacted activity logs as the default, not raw memory payloads.

## Layer Comparison

### Native Hermes Memory

Role: always-visible notes and searchable session archive.

Total Recall relationship: replaces the fragile part of long-term recall while leaving Hermes native notes useful for tiny always-visible preferences.

Score for our stack: 62/100.

### Total Recall

Role: Hermes MemoryProvider and continuity authority.

What it stores: ledger events, reduced state, checkpoints, anchors, reports, incidents, external-memory candidates, derived indexes, Knowledge Engine graph, compiled truth, synthesis, evaluation, and provider-report artifacts.

Best use: verified memory, restart recovery, compaction recovery, provenance-sensitive recall, cross-agent handoff.

Score: 94/100.

### GBrain

Role: personal/company brain repo with MCP/CLI/skills.

What it stores: markdown pages, compiled truth, timelines, typed links, embeddings/search structures, page versions, graph data.

Best use: operator-owned knowledge base, people/company/project graph, daily briefing, meeting/email/doc ingestion, readable knowledge maintenance.

Score for our stack: 89/100, but as an adjunct brain layer rather than a replacement for Total Recall.

### Hindsight

Role: memory platform with recall and reflection.

What it stores: structured memory banks designed for retain/recall/reflect.

Best use: managed recall/synthesis over memory banks, multi-agent shared memory, productized memory service.

Score: 88/100 as external candidate provider; lower as continuity authority because it is not our local ledger.

### Zep / Graphiti

Role: temporal knowledge graph and context graph engine.

What it stores: entities, relationships, and temporal validity history over conversations/business data/documents.

Best use: product-scale temporal context, stale-fact invalidation, graph-backed agent applications.

Score: 90/100 as graph memory reference architecture; not a Hermes continuity authority.

### Mem0

Role: managed memory layer with fast setup and hybrid/entity-linked retrieval.

What it stores: extracted memories, embeddings, metadata, entity links, user/agent/session memory depending on configuration.

Best use: quick production memory in apps where hosted infrastructure is acceptable.

Score: 84/100 for our stack; useful as a candidate adapter, not authority.

### Letta

Role: stateful agent runtime.

What it stores: memory blocks, messages, tools/runs/steps, archival memory, shared blocks.

Best use: when the runtime itself should manage stateful agents rather than plugging memory into Hermes.

Score: 81/100 for our stack; strong concepts, different layer.

## Recommended Stack Direction

Keep Total Recall as the Hermes MemoryProvider and continuity authority.

Use GBrain-style ideas where they strengthen Total Recall without weakening the trust model:

1. Keep compiled-truth projections generated from ledger events, then add page-versioned exports as derived artifacts.
2. Keep graph inspect/traverse commands evidence-locked, then add richer entity pages.
3. Add scheduled synthesis/maintenance jobs with receipts.
4. Add stale fact invalidation with explicit valid-from/valid-to semantics.
5. Add remote MCP/admin only after auth, redacted logs, and fail-closed verification are first-class.
6. Add adapter imports from GBrain/Hindsight/Mem0/Zep into `external-memory/quarantine`, never direct ledger writes.

The clean architecture is:

```text
Hermes Agent
  -> Total Recall MemoryProvider
    -> Total Recall core authority
      -> ledger -> state -> checkpoint -> anchor
      -> Knowledge Engine derived graph/query/synthesis/eval
      -> optional external adapters as candidate sources only

GBrain / Zep / Hindsight / Mem0
  -> optional candidate providers, federation targets, or import sources
  -> quarantine / review / owner promotion
  -> canonical ledger event only after promotion
```

## Article Thesis

The memory category is splitting into two schools.

One school optimizes memory richness: graph, markdown, hybrid search, reflection, entity linking, managed context. GBrain, Zep, Hindsight, Mem0, and Letta all live here in different ways.

The other school optimizes memory authority: what can the agent safely treat as durable truth after a long session, tool crash, compromised index, or handoff? Total Recall is moving into that lane.

For Hermes, that is the right lane. Hermes does not only need a brain. It needs a memory system that can answer: what did we know, when did we know it, who is allowed to see it, what evidence supports it, and has the continuity chain been tampered with?

That is Total Recall's job.

## Sources

- Total Recall repository: `README.md`, `docs/hermes.md`, `docs/architecture.md`, `src/total_recall_core/knowledge.py`, `hermes-plugin/total-recall/__init__.py`, plus the project test, privacy scan, and install smoke stack.
- GBrain docs: https://github.com/garrytan/gbrain/blob/master/docs/GBRAIN_V0.md
- GBrain remote MCP deployment docs: https://github.com/garrytan/gbrain/blob/master/docs/mcp/DEPLOY.md
- Vectorize GBrain explainer: https://vectorize.io/articles/what-is-gbrain
- Vectorize GBrain vs Hindsight comparison: https://vectorize.io/articles/gbrain-vs-hindsight
- Hindsight Reflect docs: https://docs.hindsight.vectorize.io/reflect/
- Mem0 platform overview and v3 migration docs: https://docs.mem0.ai/platform/overview and https://docs.mem0.ai/migration/oss-v2-to-v3
- Zep Graphiti product page: https://www.getzep.com/platform/graphiti/
- Letta stateful agents docs: https://docs.letta.com/guides/core-concepts/stateful-agents
