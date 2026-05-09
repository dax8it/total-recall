# Fortification Scorecard

Date: 2026-05-09

## Current Status

| Area | Status | Evidence |
|---|---:|---|
| Standalone repo | PASS | Core, CLI, tests, docs, and Hermes plugin live in this repo. |
| Core unit tests | PASS | `python -m pytest -q` -> 13 passed. |
| Hermes plugin lifecycle tests | PASS | Tests cover initialize, sync, search, checkpoint, verify, rehydrate, pre-compress, session switch, context-threshold auto-rehydrate, and fail-closed tamper behavior. |
| Fresh install smoke | PASS | `scripts/install_smoke.sh` installs package in a fresh virtualenv and runs health, ingest, search, checkpoint, verify, and rehydrate. |
| Privacy scan | PASS | `python scripts/privacy_scan.py` reports no local paths or sensitive configuration strings. |
| CI fortification | PASS | GitHub Actions runs privacy scan, package install, pytest, and install smoke. |
| Verified rehydration | PASS | Rehydrate calls verify first; auto-rehydrate injects FAIL_CLOSED warning instead of memory on tamper-like verification failure. |
| Derived index trust model | PASS | Tests cover tampered SQLite/FTS index rebuild from ledger during verify. |

## Remaining Hardening

| Area | Priority | Notes |
|---|---:|---|
| Ed25519 anchors | High | Current anchors use local HMAC. Ed25519 would allow public-key verification without exposing the signing secret. |
| Export/import bundle | High | Add `total-recall export`, `import`, and `doctor` for backup/restore workflows. |
| Broader tamper matrix | Medium | Add tests for deleted/reordered ledger events, modified checkpoint hash, missing receipts, and external promotion edge cases. |
| Hermes install docs | Medium | Split README Hermes content into `docs/hermes.md` with setup/troubleshooting. |
| Release tagging | Medium | Tag after the next hardening batch, e.g. `v1.2.0`. |
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
13 passed
Install smoke passed.
```
