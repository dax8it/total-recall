# HF Portable Clone Verification Wizard Implementation Plan

> For real Hermes profile workers: Filippo is orchestrator. Huggy implements. Smarty reviews. Do not treat this as a delegate-subagent task.

Goal: Add a visual, non-terminal Hugging Face Portable Clone Verification Wizard to the Total Recall Control Panel.

Architecture: Keep v1 non-destructive. The wizard may export/upload an encrypted portable clone, download it back, restore into a temporary test home, and run verify + trust gate. It must never replace active memory. Green means restored-copy verified, not upload success.

Target repo/worktree: <repo>/total-recall/.worktrees/hf-remote-backup-wizard
Base commit: 30d6fc6ad82796e878b2957619637e7970f2a080
Branch: feature/hf-remote-backup-wizard

Non-negotiable safety rules:
- No token/passphrase in API responses, Activity Console, tests, reports, manifests, git, or logs.
- HF remote is encrypted transport only, not authority.
- Active Total Recall home must not be replaced or mutated by restore-test.
- Restore-test must use a fresh temp Total Recall home.
- Repo must be verified private before upload can be green.
- Green only after fresh remote download + temp restore + verify + trust gate pass.
- Keep destructive active replacement out of v1.

Existing relevant files:
- src/total_recall_core/dashboard.py
- src/total_recall_core/api.py
- src/total_recall_core/cli.py
- tests/test_dashboard.py
- tests/test_core.py

Existing useful primitives:
- TotalRecallCore.portable_clone_export(...)
- TotalRecallCore.portable_clone_restore(...)
- TotalRecallCore.verify(...)
- TotalRecallCore.trust_gate_run(...)
- Existing /api/hf/status and /api/portable/status dashboard endpoints.

Tasks:

## Task 1: Add wizard status model and redacted API surface

Files:
- Modify: src/total_recall_core/dashboard.py
- Modify: tests/test_dashboard.py

Add endpoint:
- GET /api/hf/wizard/status

Response shape:
- ok: true
- schema: total-recall-hf-wizard-v1
- home: active core home path
- hf: existing redacted HF status
- portable: existing portable status
- session: { passphrasePresent: boolean, tokenValueVisible: false }
- repo: { repoId, exists, private, status }
- lastExport: null or redacted summary
- lastRestoreTest: null or redacted summary
- readyForGreen: boolean
- activeRestore: { enabled: false, reason: string }

Tests:
- Status does not expose HF_TOKEN or TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE.
- tokenValueVisible is always false.
- activeRestore.enabled is false.

## Task 2: Add in-memory passphrase session endpoints

Files:
- Modify: src/total_recall_core/dashboard.py
- Modify: tests/test_dashboard.py

Add endpoints:
- POST /api/hf/session/passphrase
- POST /api/hf/session/clear

Behavior:
- Accept passphrase from JSON body.
- Store in process memory only with TTL if practical.
- Return only passphrasePresent true/false.
- Never echo passphrase.
- Clear endpoint removes it.

Tests:
- POST response never contains secret.
- Subsequent status only says present.
- Clear makes present false.

## Task 3: Add private HF dataset validate/create endpoints

Files:
- Modify: src/total_recall_core/dashboard.py
- Modify: tests/test_dashboard.py

Add endpoints:
- POST /api/hf/repo/validate
- POST /api/hf/repo/create

Behavior:
- repo id must be owner/name.
- Dataset repo only.
- Prefer existing hf CLI because this repo currently uses it; do not add a new dependency unless already present.
- Use subprocess argv list, no shell.
- Validate private if CLI/API can determine it. If visibility unknown, status must not be green; return visibility unknown.
- Create must use private dataset: hf repo create <repoId> --type dataset --private --exist-ok.

Tests:
- Invalid repo id rejected.
- Public/unknown visibility is not green.
- No token leaks from fake CLI stdout/stderr.

## Task 4: Add export/upload endpoint

Files:
- Modify: src/total_recall_core/dashboard.py
- Possibly modify: src/total_recall_core/api.py if needed to redact upload reports
- Modify: tests/test_dashboard.py and/or tests/test_core.py

Add endpoint:
- POST /api/hf/export-upload

Behavior:
- Require passphrase present.
- Require repo id.
- Prefer private repo validation before upload; if visibility is unknown, allow explicit warning state but do not set readyForGreen.
- Call core.portable_clone_export with provider=huggingface, upload=True, repo_id, passphrase.
- Store only redacted lastExport in dashboard session.
- Return eventCount, cloneId, status, bundle basename, manifest basename, upload ok.
- Never return passphrase or token.

Tests:
- Upload response has status and eventCount.
- Secret not present.
- Upload success alone does not set readyForGreen.

## Task 5: Add restore-test endpoint

Files:
- Modify: src/total_recall_core/dashboard.py
- Modify: tests/test_dashboard.py

Add endpoint:
- POST /api/hf/restore-test

Behavior:
- Require passphrase and repo id.
- Download latest matching clone artifacts from HF into temp staging dir. For tests, allow fake CLI / local files.
- Restore into tempfile.mkdtemp(prefix="total-recall-hf-restore-test.").
- Instantiate TotalRecallCore with that temp home.
- Run portable_clone_restore(..., replace=True) against temp home only.
- Run verify.
- Run trust_gate_run(persist=True).
- Compare restored ledger event count/hash against exported manifest/source where possible.
- Store redacted lastRestoreTest.
- readyForGreen only true when restore, verify, trust gate pass and failedRequired == 0.

Tests:
- Restore-test uses temp home, not active home.
- Active home event count unchanged.
- Wrong passphrase returns red/fail.
- Green only after restore + verify + trust.

## Task 6: Add visual wizard UI

Files:
- Modify: src/total_recall_core/dashboard.py
- Modify: tests/test_dashboard.py

Add UI panel:
- Title: Hugging Face Backup Wizard
- Step 1: HF Auth
- Step 2: Private Dataset
- Step 3: Encryption Passphrase
- Step 4: Export + Upload
- Step 5: Restore Test
- Final: Remote Backup Verified / not green yet

Buttons:
- Refresh status
- Validate private dataset
- Create private dataset
- Save passphrase for this session
- Clear passphrase
- Export encrypted clone and upload
- Restore into temporary test home

UI copy:
- “Uploaded is not green. Restorable + verified + trust-gated is green.”
- “Active memory was not replaced.”

Tests:
- Dashboard HTML contains wizard title and key buttons/copy.

## Task 7: Verification

Run from worktree:
- PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_dashboard.py
- PYTHONPATH=src .venv/bin/python -m pytest -q
- PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/privacy_scan.py
- git diff --check

Commit:
- Commit all implementation and tests on branch feature/hf-remote-backup-wizard.
- Commit message: Add HF portable clone verification wizard

Worker output required:
- Commit hash
- Tests run with exact output
- Any known limitations
- Confirmation that no active-memory replacement is exposed
