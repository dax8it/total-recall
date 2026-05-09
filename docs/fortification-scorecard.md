# Fortification Scorecard

Date: 2026-05-09

## Current Status

| Area | Status | Evidence |
|---|---:|---|
| Standalone repo | PASS | Core, CLI, tests, docs, and Hermes plugin live in this repo. |
| Core unit tests | PASS | `python -m pytest -q` -> 23 passed. |
| Hermes plugin lifecycle tests | PASS | Tests cover initialize, sync, search, checkpoint, verify, rehydrate, pre-compress, session switch, context-threshold auto-rehydrate, and fail-closed tamper behavior. |
| Fresh install smoke | PASS | `scripts/install_smoke.sh` installs package in a fresh virtualenv and runs health, ingest, search, checkpoint, verify, rehydrate, doctor, export, import, restored verify, backup run, and backup status. |
| Privacy scan | PASS | `python scripts/privacy_scan.py` reports no local paths or sensitive configuration strings. |
| CI fortification | PASS | GitHub Actions runs privacy scan, package install, pytest, and install smoke. |
| Verified rehydration | PASS | Rehydrate calls verify first; auto-rehydrate injects FAIL_CLOSED warning instead of memory on tamper-like verification failure. |
| Derived index trust model | PASS | Tests cover tampered SQLite/FTS index rebuild from ledger during verify. |
| Ed25519 anchors | PASS | New checkpoints use local Ed25519 signatures with public keys stored beside the signing key; legacy HMAC anchors still verify. |
| Export/import/doctor | PASS | CLI covers portable bundles, manifest verification, unsafe tar rejection, restore verify, and doctor reports. |
| Broader tamper matrix | PASS | Tests cover anchor tamper, ledger text changes, deleted/reordered ledger events, checkpoint mutation, missing anchors, and anchor hash mutation. |
| Hermes install docs | PASS | Dedicated setup, smoke, provider, rehydrate, backup, and troubleshooting guide lives in `docs/hermes.md`. |
| Backup dashboard | PASS | `total-recall dashboard` serves a local management UI; `backup run/status` covers export, doctor, verify, and retention. |

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
23 passed
Install smoke passed.
```
