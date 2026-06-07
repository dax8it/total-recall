# Portable Clone Storage and Loop Ledger

Total Recall stays the continuity and trust layer. Agents execute elsewhere; Total Recall records durable evidence, restore points, and verification outcomes.

## Phase 1 — encrypted Hugging Face portable clone storage

Status: implemented in core/CLI as encrypted bundle export and restore bootstrap.

Contract:

- Remote storage only receives encrypted portable-clone envelopes and non-secret manifests.
- Plaintext exports are created only in a temporary local directory.
- Encryption is AES-256-GCM with PBKDF2-HMAC-SHA256 key derivation.
- The passphrase comes from `TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE` or an explicit local CLI argument.
- Restore must decrypt, verify plaintext hash, import the bundle, and run checkpoint/anchor verification before the clone is trusted.
- Hugging Face is treated as portable agent clone storage, ideally a private dataset or bucket.

Commands:

```bash
export TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE='use-a-real-secret'
PYTHONPATH=src .venv/bin/python -m total_recall_core.cli portable-clone export \
  --out-dir ~/total-recall-portable-clones \
  --provider huggingface \
  --repo-id USER/PRIVATE_DATASET

PYTHONPATH=src .venv/bin/python -m total_recall_core.cli portable-clone restore \
  ~/total-recall-portable-clones/total-recall-portable-clone-*.tar.gz.enc \
  --replace

PYTHONPATH=src .venv/bin/python -m total_recall_core.cli verify
PYTHONPATH=src .venv/bin/python -m total_recall_core.cli trust verify --format text
```

Dashboard rule:

- Before a real adapter exists: `planned encrypted`.
- Once encrypted portable-clone export/restore exists: `available encrypted`.

## Phase 2 — loop event ledger

Status: implemented in core/CLI as append-only loop events.

Loop event schema: `total-recall-loop-event-v1`.

Events:

- `loop start`: creates a loop id and records goal, project, agent, and optional worktree.
- `loop note`: records progress evidence.
- `loop verify`: records verification result and evidence.
- `loop complete`: closes the loop as completed or cancelled.
- `loop inbox`: derives active loops from the ledger; no separate authority.

Commands:

```bash
PYTHONPATH=src .venv/bin/python -m total_recall_core.cli loop start \
  --goal 'Daily repo triage' \
  --project /path/to/total-recall \
  --agent sparky \
  --worktree /path/to/total-recall/.worktrees/daily-triage

PYTHONPATH=src .venv/bin/python -m total_recall_core.cli loop note LOOP_ID \
  --text 'Reviewed dirty tree and failing checks.' \
  --phase discovery \
  --evidence 'git status --short'

PYTHONPATH=src .venv/bin/python -m total_recall_core.cli loop verify LOOP_ID \
  --status PASS \
  --summary 'Focused tests passed.' \
  --evidence 'pytest tests/test_core.py -q'

PYTHONPATH=src .venv/bin/python -m total_recall_core.cli loop complete LOOP_ID \
  --status DONE \
  --summary 'Loop completed and verified.'

PYTHONPATH=src .venv/bin/python -m total_recall_core.cli loop inbox
```

## Phase 3 — Hermes profile workers / Sparky / Smarty / Codex

Status: contract only; do not auto-run agents from Total Recall.

Boundary:

- Hermes profile workers, Sparky/Smarty, and Codex execute in isolated worktrees.
- Total Recall records goals, notes, verification evidence, completion, and rehydration context.
- Total Recall does not become an executor or scheduler.
- Workers must call `loop start` before work and `loop verify` before claiming success.
- Rehydration for a worker must come from verified checkpoints/trust gates, not raw unverified logs.

Minimum worker handshake:

1. Create worktree.
2. `loop start --goal ... --agent ... --worktree ...`.
3. Do the work outside Total Recall.
4. `loop note` with material evidence.
5. Run tests/checks.
6. `loop verify --status PASS|FAIL --evidence ...`.
7. `loop complete` only after verification.
8. Human/operator reviews inbox before promotion or merge.

## Phase 4 — scheduled autonomous loops

Status: design contract only; schedule after Phase 3 worker handshake is stable.

Candidate loops:

- daily repo triage
- stale task scan
- memory-learning review
- backup/clone sync
- release-readiness scorecards

Safety rules:

- Scheduled loops may gather evidence and draft recommendations by default.
- Public/network writes, merges, deletes, billing actions, and cross-profile changes require explicit approval.
- Every scheduled loop must write a loop record and verification event.
- Backup/clone sync must use encrypted portable-clone bundles only.
- Release-readiness claims must be backed by trust gate output.
