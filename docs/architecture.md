# Total Recall Architecture

Total Recall is a local-first continuity and trust engine for agent memory. Most
memory systems optimize *retrieval* — find a relevant note, vector hit, or
conversation snippet. Total Recall optimizes a harder property: whether the memory
an agent is about to use can be *proven* authoritative after restarts, compaction,
import/export, profile switches, tool churn, or corruption.

This document explains how the system is put together and why.

## One authority, several projections

Total Recall has exactly one source of truth and a set of derived projections that
can always be rebuilt from it.

```text
Authority (source of truth)
  ledger/events.jsonl  ->  state/current.json  ->  checkpoints/*.json  ->  anchors/*.json

Derived projections (rebuildable, never authority)
  index/         local search caches (LanceDB, QMD, SQLite/FTS)
  knowledge/     graph, compiled truth, synthesis, evaluation, provider reports
  reports/       audit artifacts and trust-gate reports
  reviews/       Obsidian/learning review staging
  external-memory/  quarantine / promote / reject staging
```

The rules that follow from this shape:

1. The ledger is the source of truth.
2. Checkpoints pin the reduced state, event count, and last event hash.
3. Anchors sign checkpoint hashes.
4. Verify fails closed on tamper, missing anchors, or checkpoint mismatch.
5. Rehydrate verifies before assembling any context.
6. Derived indexes can be rebuilt; they are not continuity authority.
7. Generated reports are audit artifacts, not memory sources.
8. Federation and external providers require explicit authorization.

## The trust spine

The core data flow — the "trust spine" — is a pipeline where each stage is
checkable:

```text
append-only ledger (hash chain)
  -> deterministic state reduction
    -> checkpoint (pins state hash, event count, last event hash)
      -> signed Ed25519 anchor
        -> fail-closed verify
          -> rehydrate cited context
```

- **Append-only ledger.** Every memory event is a hash-chained JSONL record. New
  events also carry a hashed origin (local device id) so provenance travels with
  the data.
- **Deterministic state.** The ledger reduces to a single deterministic state
  snapshot. The same ledger always reduces to the same state.
- **Checkpoints.** A checkpoint pins the reduced state hash, the event count, and
  the last event hash, creating a verifiable restore point.
- **Signed anchors.** Anchors sign checkpoint hashes with a local Ed25519 keypair
  (`keys/anchor.ed25519`). Legacy HMAC-SHA256 anchors remain verifiable for older
  stores; new checkpoints use Ed25519.
- **Fail-closed verify.** Verification refuses to pass — and rehydrate refuses to
  produce a context block — when any of these is true: a ledger event hash is
  invalid, the hash chain is broken, a checkpoint hash mismatches, reduced state
  differs from the checkpoint, an anchor is missing, the anchor's checkpoint hash
  mismatches, or the anchor signature mismatches.

During verification, Total Recall rebuilds derived indexes from the ledger *after*
the authoritative checks. A tampered or stale index is overwritten from trusted
ledger state rather than trusted directly.

## Retrieval is derived, not authoritative

Search runs over rebuildable indexes through a ladder, in order:

```text
LanceDB vector-ish local index
QMD compatibility index
SQLite/FTS deterministic index
lexical authority-artifact scan
```

Everything under `index/` is a derived retrieval cache. It is rebuilt from
`ledger/events.jsonl` and is never the authority for continuity, checkpoint, or
rehydrate decisions. If an index is tampered with, verify overwrites it from the
ledger. This is the point: a vector hit is a convenience, not a fact.

Generated reports under `reports/` are deliberately *excluded* from retrieval so
that recall output cannot recursively become future memory input (no "summaries of
summaries" feedback loop).

## The Knowledge Engine

On top of the authoritative ledger sits a derived intelligence layer:

- **Cited recall and answers.** `knowledge query` returns synthesized answers with
  source citations; `search` returns raw cited hits.
- **Freshness.** `knowledge freshness` classifies promises, decisions, customers,
  policies, project state, and tasks as current, stale, or superseded.
- **Temporal graph timelines.** `knowledge graph timeline --at-time` separates
  "what did we know then?" from "what changed later?".
- **Evidence-locked graph.** Active entities and edges require source refs plus
  evidence hashes; graph inspect/traverse return cited evidence, not uncited
  assertions.
- **Evidence-backed synthesis.** Provisional synthesis is owner-promoted back into
  the ledger; it never writes authority silently.

All of this is derived from the ledger and can be rebuilt. See
[knowledge-engine concepts in the operational manual](operational-manual.md) and
the [memory-layer comparison](total-recall-memory-layer-comparison-2026-06-03.md).

## Continuity across machines and agents

Total Recall proves a single store is intact and also supports moving continuity
safely between machines and agents:

- **Encrypted backups** by default, with portable export/import bundles that reject
  unsafe tar paths and validate manifest hashes.
- **Device identity** separate from the store anchor key: device keys sign remote
  HEADs, leases, and checkpoint receipts.
- **Single-writer leases** so two harnesses do not write concurrently from the same
  base (avoiding split-brain).
- **Fork-import quarantine** so divergent histories are reviewed and promoted
  rather than silently merged or overwritten.
- **Handoff packets** (resume packet + device-signed HEAD + bootstrap) for
  local → online → local continuity.
- **Verified restore**: imported/restored stores append a `re_anchor` event and
  checkpoint locally; remote artifacts are transport aids, never trusted until the
  pulled ledger verifies (and, for receipt-aware flows, `verify --receipts` passes).

See [remote-backup-design.md](remote-backup-design.md) and the "Continuity Handoff"
section of the [README](../README.md).

## Where Total Recall sits in a stack

```text
Hermes Agent (or any runtime)
  -> Total Recall memory provider (optional adapter)
    -> Total Recall core authority
       ledger -> state -> checkpoint -> anchor -> verify -> rehydrate
       Knowledge Engine: derived graph / query / synthesis / evaluation
       optional external adapters as candidate sources only
```

External memory systems can act as candidate providers or import sources, but they
flow through quarantine → review → owner promotion before becoming a ledger event.
They never write authoritative state directly. See
[integrations.md](integrations.md).

## Why this design

The bet is simple: **memory should be provable before it is useful.** A retrieved
memory is not just a vector hit — it is tied back to a source event and, for strict
rehydrate paths, to verification state. When the ledger or anchor is tampered with,
Total Recall refuses trusted rehydrate instead of returning whatever an index says.
That fail-closed posture is what makes agent memory safe to depend on after a long
session, a crash, a compromised index, or a handoff.
