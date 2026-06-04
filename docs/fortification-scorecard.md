# Fortification Scorecard

Date: 2026-05-09

## Current Status

| Area | Status | Evidence |
|---|---:|---|
| Standalone repo | PASS | Core, CLI, tests, docs, and Hermes plugin live in this repo. |
| Core unit tests | PASS | `python -m pytest -q` -> 51 passed, 1 skipped. |
| Hermes plugin lifecycle tests | PASS | Tests cover initialize, sync, source ingest, search, checkpoint, verify, rehydrate, pre-compress, session switch, context-threshold auto-rehydrate, freshness, graph timeline, federation query, and fail-closed tamper behavior. |
| Fresh install smoke | PASS | `scripts/install_smoke.sh` installs package in a fresh virtualenv and runs health, ingest, document ingest, source ingest, freshness, temporal graph timeline, vault export, vault import preview/promote, federation register/query, search, checkpoint, verify, trust verify, rehydrate, doctor, export, import, restored verify, backup run, backup status, Hermes install/status, and Hermes bundle. |
| Privacy scan | PASS | `python scripts/privacy_scan.py` reports no local paths or sensitive configuration strings. |
| CI fortification | PASS | GitHub Actions runs privacy scan, package install, pytest, and install smoke. |
| Verified rehydration | PASS | Rehydrate calls verify first; auto-rehydrate injects FAIL_CLOSED warning instead of memory on tamper-like verification failure. |
| Trust gate | PASS | `total-recall trust verify` runs hard-coded real-store ledger/checkpoint/index/export-import gates plus isolated source ingest, freshness, temporal timeline, Obsidian import, federation, fixture persistence, and Hermes plugin bundle checks. |
| Derived index trust model | PASS | Tests cover tampered SQLite/FTS index rebuild from ledger during verify. |
| Ed25519 anchors | PASS | New checkpoints use local Ed25519 signatures with public keys stored beside the signing key; legacy HMAC anchors still verify. |
| Export/import/doctor | PASS | CLI covers portable bundles, manifest verification, unsafe tar rejection, restore verify, and doctor reports. |
| Document ingest | PASS | `total-recall documents ingest` imports supported files/folders into ledger-backed document events, reports skipped files, supports dry-run, and feeds search plus Knowledge Engine queries. |
| Working-context source ingest | PASS | `total-recall sources ingest` accepts meetings, email, Slack, GitHub, CRM, tickets, calendars, and agent transcripts with effective timestamps for historical recall. |
| Freshness and temporal graph | PASS | `knowledge freshness` reports current/stale/superseded memory; `knowledge graph timeline` separates as-of from later evidence. |
| Obsidian vault review loop | PASS | `total-recall vault export` and `total-recall obsidian export` generate a derived wikilinked vault; `vault import-preview` writes review artifacts without ledger writes; `vault import-promote` writes approved `obsidian_note_import` ledger events. |
| Multi-agent federation registry | PASS | `federation register/list/remove/query` stores named targets and keeps cross-agent query results explicit, authorized, and workspace-separated. |
| Broader tamper matrix | PASS | Tests cover anchor tamper, ledger text changes, deleted/reordered ledger events, checkpoint mutation, missing anchors, and anchor hash mutation. |
| Hermes install docs | PASS | Dedicated setup, smoke, provider, rehydrate, backup, and troubleshooting guide lives in `docs/hermes.md`. |
| Admin dashboard | PASS | `total-recall dashboard` serves a local remote-MCP/admin-style control center with Trust Spine gates, Knowledge Engine operations, source ingest, freshness, graph timeline, Obsidian export/import review, remote provider readiness, backup download links, launchd plist generation, and count/day retention. |
| Hermes plugin installer | PASS | `total-recall hermes install` writes a clean copy-mode plugin bundle, checks/installs `total-recall-core` into Hermes' Python, `total-recall hermes doctor` reports readiness, `total-recall hermes bundle` emits a distributable tarball with the expanded tool surface, and tests verify the installed wrapper imports through Hermes-style modules. |

## Remaining Hardening

| Area | Priority | Notes |
|---|---:|---|
| Release tagging | Medium | Tag after this hardening batch, e.g. `v1.3.0`. |
| Adapter framework | Later | Hindsight/Honcho/Mem0 should remain derived-memory candidates, never authoritative state. |

## Commands

```bash
python scripts/privacy_scan.py
python -m pytest -q
scripts/install_smoke.sh
```

Expected local result:

```text
Privacy scan passed.
51 passed, 1 skipped
Install smoke passed.
```
