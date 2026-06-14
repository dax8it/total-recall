# Total Recall Roadmap

This is a public, forward-looking view of where Total Recall is headed. It is a
direction, not a dated commitment. The guiding constraint never changes: new
capabilities must preserve the trust spine (append-only ledger → deterministic
state → signed checkpoint → Ed25519 anchor → fail-closed verify) and must keep
derived and remote artifacts non-authoritative.

For what exists today, see [architecture.md](architecture.md) and the
[operational manual](operational-manual.md).

## Shipped today

- Append-only hash-chained ledger, deterministic state reduction.
- Signed Ed25519 checkpoints/anchors; fail-closed verify and rehydrate.
- Derived retrieval ladder: LanceDB, QMD, SQLite/FTS, lexical fallback.
- Generated-report exclusion from retrieval.
- Knowledge Engine: cited query, freshness, temporal graph timelines,
  evidence-locked graph, owner-promoted synthesis, evaluation harness.
- Working-context source ingest and local document ingest.
- Obsidian/vault export with preview/promote import review.
- Encrypted backups, device identity, single-writer leases, fork-import
  quarantine, handoff packets, and verified restore.
- Explicit, read-only workspace federation.
- Operator dashboard (Trust Spine, Knowledge Engine, Workbench, Vault, Backups).
- Optional Hermes Agent memory-provider plugin.

## Near-term direction

### Deeper temporal validity
Move from point-in-time query filtering and supersession warnings toward typed
validity intervals on graph edges (valid-from / valid-to, invalidation time,
learned time), so historical relationship facts can be queried as of any moment.

### Richer, still-safe graph ontology
Expand entities and typed relationships, entity resolution, and quarantine
browsing — while keeping evidence hashes mandatory on active graph elements.

### Scheduled maintenance with receipts
Add scheduled synthesis/maintenance jobs where every promotion is owner-authorized
and produces a receipt, building on the nightly learning review.

## Medium-term direction

### Remote / admin surface (auth-first)
A remote MCP/admin surface is planned only behind authentication, redacted activity
logs by default, and fail-closed verification. The local-first, fail-closed mode
remains the foundation; remote serving is an addition, never a replacement.

### Encrypted remote backup adapters
Direct cloud adapters are intentionally gated until encryption and credential
storage (OS keychain, not the repo or ledger) are first-class. Candidate targets
include encrypted S3-compatible, Pinata/IPFS, Arweave, Google Drive, Dropbox, and
removable-drive mirrors. Remote receipts never become continuity authority.

### External memory adapters as candidates
Derived-memory bridges to systems such as Hindsight, Honcho, and Mem0, wired
strictly through the quarantine → review → owner-promotion path. See
[integrations.md](integrations.md) and
[derived-memory-adapters.md](derived-memory-adapters.md).

## Longer-term direction

### Turnkey ingestion connectors
Beyond the current normalized source-ingest command, add OAuth/API connectors and
file watchers for a daily-life ingestion pipeline (meetings, email, chat, docs).

### Managed ergonomics without losing local-first
Smoother setup, dashboards, and instrumentation for teams — added in a way that
keeps the local signed ledger as the source of truth.

## Non-goals

- The derived indexes will never become the source of truth.
- External providers will never write authoritative state directly.
- Remote transport and receipts will never replace local verification.
- Federation will never silently merge another workspace's memory.

## Contributing to the roadmap

Ideas and issues are welcome through the project repository. Contribution terms are
in [CONTRIBUTING.md](../CONTRIBUTING.md); security reports follow
[SECURITY.md](../SECURITY.md).
