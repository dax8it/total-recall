# Continuity Implementation Handoff

Date: 2026-06-10
Audience: implementing agent (Codex / Hermes worker / Claude) continuing Total Recall.
Authority: this document plus `docs/continuity-gap-analysis-2026-06-10.md`. The trust model in `README.md` ("Trust Model") is inviolable: ledger → state → checkpoint → anchor → fail-closed verify. Derived data and remote artifacts are never authority.

## Ground rules

1. Work step by step, in the numbered order below. Land each step with tests before starting the next, and report with evidence (`pytest -q` output, trust-gate output).
2. Keep green at every step: `PYTHONPATH=src python -m pytest -q`, `scripts/privacy_scan.py`, `scripts/install_smoke.sh`.
3. Extend `trust_gate_run()` with a fixture gate for each new flow (pattern: existing isolated synthetic fixture gates in api.py).
4. Additive schemas only. New event fields are optional; old events must still verify. Bump schema strings, never mutate `total-recall-export-v1` semantics silently.
5. No new heavy dependencies. `cryptography` is already present (AESGCM, Ed25519). Hugging Face via CLI (`hf`) as in `_portable_clone_hf_upload`.
6. Secrets live in env/OS keychain only. Never in ledger, reports, manifests, or git. `privacy_scan.py` must keep passing.
7. Use `loop start/note/verify/complete` (docs/portable-clone-and-loop-ledger.md) to record your own work.

## Key code locations

- Core: `src/total_recall_core/api.py` — `ingest` (~line 1860), `sync_turn` (~1904), `checkpoint` (~1966), `verify` (~2010), `rehydrate` (~2087), `export_bundle` (~2560), `import_bundle` (~2615), `portable_clone_export/restore` (~2683/2804), `backup_status/sync_status/backup_run` (~3438–3550), `_portable_clone_hf_upload` (~4449).
- Provider: `src/total_recall_core/hermes_provider.py` — lifecycle hooks (`initialize`, `prefetch`, `sync_turn`, `on_session_end`, `on_session_switch`, `on_pre_compress`, `on_turn_start`), auto-rehydrate policy (~871–1027), tool schemas/handler.
- CLI: `src/total_recall_core/cli.py`. Dashboard: `src/total_recall_core/dashboard.py` (`/api/remote/*`). Tests: `tests/test_core.py`, `tests/test_hermes_plugin.py`, `tests/test_dashboard.py`.

## Step 1 — Session Resume Packet (G1, G7)

New artifact: `continuation/<safe_session_id>/packet_<ts>.json`, schema `total-recall-resume-packet-v1`:

```json
{
  "schema": "total-recall-resume-packet-v1",
  "packet_id": "...", "created_at": "...", "session_id": "...",
  "checkpoint_id": "...", "anchor_file": "...",
  "ledger": {"event_count": 0, "last_event_hash": "..."},
  "recent_turns": [{"event_id": "...", "timestamp": "...", "text": "<verbatim, untruncated>"}],
  "open_loops": [/* from loop inbox */],
  "freshness": {/* summary buckets from knowledge freshness */},
  "compiled_truth_excerpt": "<top of knowledge truth show, capped ~4000 chars>",
  "environment": {"cwd": "...", "git_repo": "...", "git_branch": "...", "hermes_profile": "...", "device_id": "..."},
  "next_actions": ["..."]
}
```

- `recent_turns`: last N `kind=turn` events for the session from the ledger (default N=30, config `resume_packet.turns`). Verbatim — do not truncate; the ledger already holds full text.
- `next_actions`: lexical extraction from recent events (lines matching next/todo/blocker/decision patterns) + open loops. Deterministic, no model calls.
- Write triggers: `checkpoint()` (optional flag, default on for `session_end` label), provider `on_session_end`, and new CLI `total-recall handoff export --session-id S`.
- New `rehydrate(mode="resume")`: verify first (fail closed exactly like query mode); then load the newest packet whose `ledger.last_event_hash` is an ancestor of (or equal to) current; render a deterministic context block: header (checkpoint/anchor), verbatim turn tail (configurable char budget, newest-first trim), open loops, next actions, freshness warnings. Keyword mode remains the fallback when no packet exists.
- Provider: new tool `total_recall_handoff_export`; auto-rehydrate uses `mode=resume` for triggers `startup_or_gateway_restart`, `after_resume`, `after_new_session`, `after_compaction` when a packet exists.
- Packets are derived artifacts: exclude from retrieval indexing (reuse the reports-exclusion mechanism), include in `export_bundle` dirs.
- Tests: packet written on session_end; resume block contains verbatim turn text; fail-closed when ledger tampered; packet ignored when its last_event_hash is unknown to the ledger. Trust gate: fixture session → packet → resume rehydrate → assert verbatim content present.

## Step 2 — Event origin + device identity (G8)

- Generate per-machine `keys/device.ed25519`(+`.pub`) on first use; `device_id` = first 16 hex of SHA-256 of the public key. New module-level helper alongside anchor key handling.
- `ingest()` adds `"origin": {"device_id": ..., "agent": <env TOTAL_RECALL_AGENT or "">, "harness": "hermes"|"cli", "host": platform.node()}` into the hashed base. Optional on read: verification must accept events without `origin` (legacy).
- Device registry: `devices/device_<id>.json` with `device_id, label, public_key, approved_at, revoked_at, last_seen_at`. CLI: `total-recall device init|list|approve|revoke`. Self device auto-registered approved.
- IMPORTANT: device key ≠ anchor key. The anchor key signs checkpoints for a store; the device key identifies a machine and signs leases/receipts (Steps 4/5/7).
- Tests: origin present on new events; legacy ledger without origin still verifies; registry CRUD.

## Step 3 — Encrypted backups by default; key hygiene (G4)

- `export_bundle(..., include_keys=False)` default: exclude `keys/` (anchor + device private keys) from the file list. `backup_run` and portable clone pass `include_keys=True` ONLY into the encrypted path. Plain `total-recall export` warns and requires `--include-keys` to embed keys.
- `backup_run(..., encrypt=True)` default: wrap the tar.gz in the existing AES-256-GCM envelope. Key wrapping: random 32-byte data key; encrypt data key to each approved, non-revoked device public key (X25519 derived from Ed25519 keys via `cryptography`, or generate a parallel X25519 device key at `device init` — simpler, do that). Passphrase recipient (PBKDF2, reuse `_portable_clone_key`) as fallback recipient so a user with no second device can still restore.
- Envelope schema `total-recall-encrypted-backup-v1` per docs/remote-backup-design.md (bundle_sha256, ciphertext_sha256, created_at, source_device_id, checkpoint_id, event_count, last_event_hash, recipients[], provider_receipts[]).
- `backup restore <env.enc>`: try device private key, then `TOTAL_RECALL_BACKUP_PASSPHRASE`; decrypt → `import_bundle` → `verify` → report. `--plaintext` escape hatch on `backup_run` logs a warning line in the report.
- Update `backup_status`/`sync_status`/dashboard to read `.enc` envelopes' manifests (they are cleartext JSON next to ciphertext, like portable clone).
- Tests: default run produces only `.enc` + manifest; plaintext export contains no `keys/`; restore round-trip via device key and via passphrase; revoked device not in recipients.

## Step 4 — Remote HEAD + push/pull (G5)

- `HEAD.json`, schema `total-recall-remote-head-v1`: `{schema, updated_at, store_id, latest: {bundle, bundle_sha256, checkpoint_id, event_count, last_event_hash, created_at, device_id}, lease: {...|null}, receipts: [last ~100 of {checkpoint_id, state_hash, event_count, created_at, device_id, signature}]}`. Signed field: detached Ed25519 device signature over canonical JSON of `latest`+`receipts` tail.
- Provider abstraction `RemoteTarget` with two impls: `local-folder` (path) and `huggingface` (repo_id via `hf upload/download`; add `_portable_clone_hf_download` mirroring upload). Config under `federation/`? No — new `remote/targets.json`.
- CLI: `total-recall backup push [--target T]` = backup_run(encrypted) → upload bundle+manifest → update+sign HEAD. `total-recall backup pull [--target T]` = fetch HEAD → compare to local (`sync_status` logic generalized): `in_sync` no-op; `archive_ahead` download/decrypt/import/verify/rebuild indexes; `local_ahead` refuse with "push instead"; `diverged` refuse, point at Step 6 fork flow. `total-recall sync check --target T` prints relation.
- `store_id`: random UUID written once to `state/store_id` at init; guards against pulling a different agent's HEAD.
- Tests: push/pull round-trip via local-folder target in tmpdir; diverged refusal; HEAD signature verification failure refuses pull.

## Step 5 — Single-writer lease (G3)

- Lease object inside HEAD: `{holder_device_id, holder_label, acquired_at, ttl_seconds (default 3600), expires_at, signature}` signed by holder's device key.
- CLI: `total-recall lease acquire|release|status|steal --target T`. `steal` requires `--force`, writes a `lease_steal` ledger event and an incident.
- Provider enforcement: on `initialize` and every `on_turn_start` (cheap cached check, re-check every `lease.check_every_turns`), if a remote target is configured and an unexpired lease is held by another device: inject a prominent warning block and set provider read-only mode — `sync_turn`/`ingest` paths log-and-drop (or queue to `state/pending_events.jsonl` for later operator review; choose queue, it is safer). Config `lease.enforce: warn|block` default `block`.
- Handoff choreography this enables: A `lease release` + `backup push` → online agent `backup pull` + `lease acquire` → work → `lease release` + `backup push` → A `backup pull` + `lease acquire`.
- Tests: second device blocked while lease valid; expiry frees it; steal records incident.

## Step 6 — Divergence fork-import (G2)

- `total-recall sync fork-import <bundle-or-target>`: extract archive ledger to temp; find longest common prefix by event hash; verify both suffixes' internal chains; write the archive-only suffix events into `external-memory/quarantine/` as candidate items (existing quarantine machinery) with provenance `{fork_base_hash, archive_bundle, origin_device}`; never touch the local ledger.
- Promotion uses the existing external promote flow → promoted items become NEW ledger events on the local chain (re-hashed, original event preserved in metadata). Document clearly: fork-import preserves *content*, not the foreign chain.
- Dashboard: diverged state shows a "Fork import" action instead of a dead end.
- Tests: synthetic divergence (shared base, both sides append) → fork-import quarantines exactly the archive suffix; promote → content present in local search; local chain still verifies.

## Step 7 — Re-anchor + receipts (G4 tamper-evidence)

- On every successful `import_bundle`/`portable_clone_restore`/`backup pull` import: append a `re_anchor` ledger event `{restored_checkpoint_id, restored_last_event_hash, source_bundle_sha256, device_id}` signed inline by the device key (signature in metadata), then checkpoint.
- Every `checkpoint()` appends a receipt to local `anchors/receipts.jsonl`; `backup push` merges receipts into HEAD (Step 4 schema). Verification gains an optional `--receipts` cross-check: current chain must contain/extend the receipt lineage; mismatch ⇒ store-replacement warning incident.
- Tests: restore writes re_anchor; receipt lineage mismatch flags incident.

## Step 8 — Handoff bootstrap (G6, pragmatic path)

- `total-recall handoff issue --target T --session-id S`: lease release→push (Steps 4/5) + resume packet (Step 1) + emit `handoff/<id>.json` (target, bundle ref, packet ref, instructions) and a one-screen bootstrap shell script: install total-recall-core, pull, verify, trust verify, lease acquire, print resume block.
- `total-recall handoff accept <handoff.json>`: runs that sequence locally. The online agent (Hermes hosted, or any harness that can run the CLI) executes the same script — no remote MCP needed for v1.
- Defer OAuth remote MCP (R10) — keep dashboard statuses honest (`planned`).

## Step 9 — Write-path scalability guard (G9, bounded effort)

- Make per-ingest work O(append): keep an in-memory/state cache so `reduce_state` doesn't reread the whole ledger per event (incremental fold keyed by last_event_hash), and make FTS insert-only per event instead of rebuild. Full rebuild stays for verify/import. If this exceeds budget, at minimum batch `sync_turn` index updates and document the tradeoff.

## Acceptance (whole program)

1. Travel flow is two commands per side (`backup push` / `backup pull` + lease) with encryption on by default and no private keys in any plaintext artifact.
2. Restored agent's first context block contains verbatim recent turns + open loops + next actions, gated by fail-closed verify.
3. Concurrent writes from two devices are prevented (lease) and, when they happen anyway, recoverable (fork-import) — never silent merge, never lost-by-default.
4. Store replacement at the backup location is detectable (receipts/re-anchor).
5. `pytest`, privacy scan, install smoke, and `trust verify` all pass with new gates included; README + remote-backup-design statuses updated to reflect reality.
