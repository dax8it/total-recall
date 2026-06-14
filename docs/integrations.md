# Integrating Total Recall With Other Memory Systems

Total Recall is not trying to replace every memory tool. It occupies a specific
lane: a local-first continuity authority that can prove which memory is allowed to
matter. Other systems — vector stores, managed memory platforms, temporal graphs,
agent runtimes — are good at things Total Recall deliberately does not try to be.

This document explains how Total Recall relates to those systems and the one rule
that governs every integration.

## The integration rule

External systems can only ever produce **candidates**. They never write
authoritative state.

```text
external adapter / import result
  -> Total Recall external-memory quarantine
    -> review (promote / reject)
      -> promoted item becomes a ledger event
        -> checkpoint / anchor / verification cover the promoted truth
```

Adapters must not write directly to the ledger, reduced state, checkpoints, or
anchors. They may generate semantic results, reflections, extracted facts, and
receipts; everything they produce is a cited candidate until an owner promotes it.
This keeps the trust spine intact no matter how rich the upstream system is.

## Hermes Agent (Nous Research)

Total Recall ships an optional memory-provider plugin for Hermes Agent. This is a
real `MemoryProvider`, not a skill pretending to be a database: it participates in
Hermes lifecycle hooks (prefetch, sync turn, session switch, pre-compress,
checkpoint, verify, rehydrate).

The division of responsibility:

- **Hermes owns** when old chat is compacted.
- **Total Recall owns** whether prior memory is safe to reuse afterward.

The simplest profile policy treats compaction and auto-rehydrate as one visible
"context risk zone" by aligning the two thresholds. See [hermes.md](hermes.md) and
[install.md](install.md) for setup.

## How Total Recall compares to other memory layers

Total Recall is one school of memory design (provable authority). Most other tools
are the other school (memory richness). They compose well precisely because they
optimize different things. A full scorecard is in the
[memory-layer comparison](total-recall-memory-layer-comparison-2026-06-03.md);
in short:

| System | Strongest at | Role alongside Total Recall |
|---|---|---|
| Native Hermes memory | always-visible small notes, local session archive | keep for tiny always-visible preferences |
| Zep / Graphiti | product-scale temporal knowledge graph, stale-fact invalidation | graph memory reference / candidate source |
| Hindsight | structured retain / recall / reflect, synthesis over memory banks | external candidate provider |
| Mem0 | fast managed memory, hybrid / entity-linked retrieval | candidate adapter, not authority |
| Letta | stateful agent runtime, editable memory blocks | different layer (runtime owns state) |
| GBrain | markdown brain repo, self-wiring graph, MCP/CLI/skills | adjunct brain / federation target |

Total Recall's contribution to any of these pairings is the same: ledger event,
hash chain, checkpoint, signed anchor, citation, evidence hash, scope, provider
receipt, and fail-closed verification.

## Derived-memory adapters (planned)

External semantic adapters are intentionally deferred and, when added, will be
implemented as derived-memory bridges rather than replacements for local authority.
Planned candidates:

- **Hindsight** — retain, recall, reflect, entity extraction, synthesis
- **Honcho** — user/agent peer modeling, cards, context, conclusions
- **Mem0** — fact extraction, dedupe, profile recall, semantic search

See [derived-memory-adapters.md](derived-memory-adapters.md) for the adapter trust
boundary, and [roadmap.md](roadmap.md) for sequencing.

## Federation between Total Recall stores

Multiple Total Recall agents/workspaces can share memory without silently merging
it. Federation is read-only and explicit:

```bash
total-recall federation register agent-beta /path/to/agent/total-recall --scope public
total-recall federation query --query "support promise" --target agent-beta --authorize
```

A federated query stays local unless explicitly authorized, and authorized results
remain workspace-separated. There is no global memory soup.

## Retrieval backends

Total Recall's own retrieval layers (LanceDB, QMD, SQLite/FTS, lexical fallback)
are derived indexes, not integrations in the authority sense — they are rebuilt
from the ledger and never trusted as truth. See [architecture.md](architecture.md).
