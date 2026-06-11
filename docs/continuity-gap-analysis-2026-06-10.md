# Total Recall Continuity Gap Analysis

Date: 2026-06-10
Scope: review of README, docs (remote-backup-design, portable-clone-and-loop-ledger, memory-layer comparison, hermes handoff), `src/total_recall_core/api.py`, `src/total_recall_core/hermes_provider.py`, dashboard, and the hermes-plugin bundle.
Goal under test: local-first continuity that can (a) compact and rehydrate without losing context, (b) restore an agent on another machine, and (c) hand a live session to an online agent and back without split-brain.

## Verdict

The trust spine is real and well built: append-only hash-chained ledger, deterministic state reduction, Ed25519-anchored checkpoints, fail-closed verify/rehydrate, encrypted portable clone, and a hard-coded trust gate. Where the architecture is weak is everything *between two stores*: resumption fidelity, transport, write coordination, and divergence recovery. Today Total Recall proves a single store is intact; it does not yet move a live session safely between machines or agents.

## Gaps (ordered by impact on the continuity goal)

### G1 — Rehydration is retrieval, not resumption (P0)

`rehydrate()` (api.py ~2087) runs verify, then a keyword search, and returns up to 8 snippets truncated to 500 chars. Quality depends on guessing the right query. `on_session_end` keeps only the last 24 messages truncated at 1,200 chars; `on_pre_compress` stores a 1,600-char *query digest*, not the content being compacted (individual turns are saved via `sync_turn`, which mitigates this, but only for turns that flowed through the provider). There is no deterministic "resume packet": verbatim recent turns, open loops, next actions, environment fingerprint, freshness summary. A restored agent gets integrity-verified fragments, not its working state.

### G2 — The linear hash chain makes divergence unrecoverable by design (P0/P1)

Every event chains on `_last_event_hash()` of one global ledger. `sync_status()` (api.py ~3460) correctly detects `diverged`, but the only path forward is discarding one side. If machine A and machine B (or local agent and online agent) both write from the same base, one machine's work is lost. There is no fork-import, no quarantine of divergent events, no per-device ledger segments. Detection without recovery is a dead end for the multi-machine goal.

### G3 — No single-writer coordination (P0 for handoff)

Nothing prevents two harnesses from writing concurrently from the same restored base — the exact scenario the online-handoff goal creates. There is no session lease, no ownership manifest, no "this store is checked out by device X until T." Split-brain is not an edge case here; it is the default outcome of the intended workflow.

### G4 — Plaintext backups ship the anchor private key (P0, security)

`export_bundle()` includes `keys/` (the Ed25519 *private* anchor key), and `backup_run()` writes that tarball unencrypted to the backup dir — including iCloud/Drive folders per the documented travel flow. Anyone holding a backup can rewrite the ledger, recompute the chain, and re-sign anchors: the trust spine does not survive a compromised backup location. Related: checkpoint hashes are never anchored anywhere external, so whole-store replacement is undetectable. The device-recipient envelope encryption in `docs/remote-backup-design.md` is design-only; the portable clone is encrypted but passphrase/PBKDF2 only, with no device/recipient model.

### G5 — One-way, manual transport; no remote HEAD (P1)

Upload exists (`_portable_clone_hf_upload`), download does not. `portable-clone restore` takes a local path; `sync_status` inspects exactly one local directory. Machine B has no command to discover, fetch, and verify the latest archive ("what is HEAD, who wrote it, when"). The travel flow is a manual checklist, and backup itself is operator-triggered — continuity depends on discipline, not mechanism (no auto-backup on checkpoint/session_end).

### G6 — No online continuation surface (P1/P2)

The dashboard binds 127.0.0.1 and reports `remoteMcp: planned`, `auth: local-only-no-oauth`. An online agent can neither query the store remotely nor accept a handoff. Even without remote MCP, there is no handoff bootstrap (issue a packet + encrypted clone + lease, accept on the other side).

### G7 — Memory is backed up; the session is not (P1)

Total Recall captures ledger events, but the harness's own session artifacts (Hermes transcript, profile config) are outside the bundle. A restored machine starts a *new* session with a rehydrate block. "Continue the session" in the literal sense requires a continuation packet that carries (or reconstructs) the conversational tail and profile context.

### G8 — Events carry no origin identity (P0, cheap now, impossible later)

Events have no `device_id`/agent/host fields. The remote-backup design recommends `source_device_id` on envelopes, but the ledger itself can't say which machine wrote what. Any future merge, lease, or forensics work needs this; retrofitting after stores diverge is far harder than adding it now.

### G9 — Scalability of the write path threatens continuity at scale (P2)

`ingest()` runs full `reduce_state(write=True)` plus an FTS index rebuild *per event*, and state embeds every memory. Per-turn syncing on a long-lived store trends O(n²). Not a correctness gap, but it will eventually pressure operators to disable per-turn sync — which silently destroys the continuity guarantee.

## Recommendations (phased)

Phase 1 — resumption + identity + key hygiene (no wire-format risk):
R1. Session Resume Packet v1 + `rehydrate --mode resume` (fixes G1, G7).
R2. `origin{device_id, agent, harness, host}` on every event; per-machine device keypair + registry with approve/revoke (fixes G8, enables G3/G7-adjacent work).
R3. Encrypt backups by default with device-recipient envelopes; exclude `keys/` from plaintext exports (fixes G4 first half).

Phase 2 — transport + coordination:
R4. Remote `HEAD.json` + `backup push/pull/sync-check` for local-folder and Hugging Face providers (fixes G5).
R5. Signed single-writer lease in HEAD with TTL; provider blocks/warns on writes without the lease (fixes G3).
R6. Auto-backup on checkpoint/session_end with debounce (fixes G9-adjacent operator burden, G5).

Phase 3 — divergence + tamper-evidence:
R7. Fork-import: divergent archive suffix → external-memory quarantine → review/promote; never silent merge (fixes G2 without breaking the chain).
R8. Re-anchor event on restore (signed by device key) + checkpoint receipts appended to HEAD history; external anchoring makes store replacement detectable (fixes G4 second half).

Phase 4 — online continuation:
R9. `handoff issue/accept`: encrypted clone + resume packet + lease transfer + bootstrap script an online agent can run (fixes G6 pragmatically).
R10. Remote read-only MCP with OAuth, only after R3/R5/R8 (the comparison doc already commits to this ordering).

Full schemas, file pointers, acceptance criteria, and test plan: `docs/continuity-implementation-handoff-2026-06-10.md`. Agent prompt (≤4,000 chars): `docs/continuity-handoff-prompt.txt`.
