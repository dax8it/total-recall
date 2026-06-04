# Hermes Handoff: Total Recall Public Plugin And Verification Gate

Date: 2026-06-04
Repo: this checkout
Branch: `main`
Source of handoff: Codex desktop session after completing Total Recall public-facing plugin, company-brain, document/vault, and hard-coded verification work.

## Current Objective

Continue work on Total Recall as the public-facing Hermes memory continuity layer.
The latest completed goal was:

> Verify and harden Total Recall so the recent implementation is enforced by immutable, hard-coded gates/checks/persistence/trust verification rather than vibes, docs, or skill instructions. Add actual logic that proves the key flows executed as expected.

This goal is complete in the current working tree.

## Product Position

Total Recall is being positioned as a local-first Hermes MemoryProvider and continuity authority:

- Append-only ledger with hash chaining.
- Deterministic state reduction.
- Signed checkpoints and anchor verification.
- Fail-closed verify and rehydrate.
- Cited local recall and Knowledge Engine.
- Basic document context ingest so users can drop files/folders in without needing GBrain for simple docs.
- Obsidian-compatible vault export plus explicit edited-note preview/promote import.
- Working-context source ingest for meetings, email, Slack, GitHub, CRM, tickets, calendars, and agent transcripts.
- Freshness reporting for current/stale/superseded promises, decisions, customers, policies, project state, and tasks.
- Temporal graph timeline for "what did we know then?" versus "what changed later?"
- Named multi-agent/workspace federation with explicit authorization and workspace-separated results.
- Product-facing Hermes plugin installer and distributable plugin bundle.
- Hard-coded trust gate for day-one/release execution verification.

## Most Recent Implementation

The latest addition is a runtime trust gate, not a docs-only checklist.

Core API:

- `TotalRecallCore.trust_gate_run(persist=True)`
- `TotalRecallCore.trust_gate_status()`
- Schema: `total-recall-trust-gate-v1`
- Persists reports under the Total Recall home:
  - `reports/trust_gate_<gate_id>_<timestamp>.json`
  - `reports/trust_gate_<gate_id>_<timestamp>.md`
  - `reports/trust_gate_latest.json`

CLI:

- `total-recall trust verify`
- `total-recall trust status`
- `--format json|text`
- `--no-persist`

Hermes plugin:

- New tool: `total_recall_trust_verify`
- Added to provider schema, handler, generated plugin bundle, repo plugin bundle, and plugin docs.

Dashboard:

- New POST endpoint: `/api/trust/verify`
- Trust Spine includes "Execution trust gate".
- UI has a Trust Gate button.

Install smoke:

- `scripts/install_smoke.sh` now runs `trust verify` after checkpoint/verify.

## What The Trust Gate Actually Verifies

Real-store gates:

- Ledger hash chain reduces successfully.
- Latest checkpoint and signed anchor exist and pin the current ledger state.
- Core retrieval index is fresh or rebuildable from ledger authority.
- Knowledge Engine derives from current ledger and graph evidence has citations.
- Real store export/import round-trip restores the same ledger point.

Isolated synthetic fixture gates:

- Working-context source ingest writes hash-chained source ledger events with effective timestamps.
- Freshness report marks one promise current and one superseded.
- Temporal graph timeline separates as-of evidence from later changes.
- Obsidian import preview writes a review artifact without mutating the ledger.
- Obsidian import promote writes explicit `obsidian_note_import` ledger events.
- Federation refuses unauthenticated cross-workspace reads.
- Authorized federation returns cited workspace-separated results without silent merge.
- Fixture checkpoint, verify, export, and import preserve ledger state.
- Hermes plugin generator, checked-in repo plugin, and distributable tarball expose the required tool surface.

Failure behavior:

- Any required gate failure makes the result `ok: false`, `status: FAIL_CLOSED`.
- Persisted failures create a fail-closed incident.
- This is intended to prevent "it seems fine" release claims.

## Validation Already Run

From the repo root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q
```

Result:

```text
51 passed, 1 skipped in 9.30s
```

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/privacy_scan.py
```

Result:

```text
Privacy scan passed.
```

```bash
PYTHONDONTWRITEBYTECODE=1 ./scripts/install_smoke.sh
```

Result:

```text
Install smoke passed.
```

```bash
git diff --check
```

Result: clean.

Also passed focused checks:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest \
  tests/test_core.py::test_trust_gate_persists_hardcoded_execution_report \
  tests/test_core.py::test_trust_gate_fails_closed_without_checkpoint \
  tests/test_core.py::test_trust_gate_cli_text_output -q
```

Result:

```text
3 passed
```

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/test_hermes_plugin.py -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/test_dashboard.py -q
```

Results:

```text
4 passed
1 passed
```

## Important Command Note

The global `total-recall` executable in the user-local bin directory appears stale and does not expose the newest commands.
The repo venv console script also appears stale.

Use the source-module invocation for current-tree validation:

```bash
PYTHONPATH=src .venv/bin/python -m total_recall_core.cli --help
PYTHONPATH=src .venv/bin/python -m total_recall_core.cli trust verify --format text
```

Or reinstall the package into the local venv before using `.venv/bin/total-recall`.

## Current Git State

There are many modified and untracked files from the completed work. Do not revert them.

Modified tracked files include:

- `README.md`
- `docs/backup-dashboard.md`
- `docs/fortification-scorecard.md`
- `docs/hermes.md`
- `docs/release-checklist.md`
- `hermes-plugin/total-recall/__init__.py`
- `hermes-plugin/total-recall/plugin.yaml`
- `pyproject.toml`
- `scripts/install_smoke.sh`
- `src/total_recall_core/api.py`
- `src/total_recall_core/cli.py`
- `src/total_recall_core/dashboard.py`
- `tests/test_core.py`
- `tests/test_hermes_plugin.py`

Important untracked files include:

- `docs/document-ingest.md`
- `docs/knowledge-engine-api.md`
- `docs/knowledge-engine-architecture.md`
- `docs/knowledge-engine-decisions.md`
- `docs/knowledge-engine-risks.md`
- `docs/knowledge-engine-roadmap.md`
- `docs/knowledge-engine-runbook.md`
- `docs/knowledge-engine-tribal-knowledge.md`
- `docs/obsidian-vault-export.md`
- `docs/total-recall-memory-layer-comparison-2026-06-03.md`
- `hermes-plugin/total-recall/README.md`
- `scripts/install_hermes_plugin.sh`
- `src/total_recall_core/fixtures/`
- `src/total_recall_core/hermes_installer.py`
- `src/total_recall_core/hermes_provider.py`
- `src/total_recall_core/knowledge.py`
- `tests/test_dashboard.py`
- `tests/test_hermes_installer.py`
- `uv.lock`

## Suggested Next Steps

Do not start changing files until the user gives a new instruction in Hermes.

When the user resumes here, useful next moves are:

1. Reinstall the local venv package or use source-module commands consistently.
2. Run `PYTHONPATH=src .venv/bin/python -m total_recall_core.cli trust verify --format text` against a deliberate test home after checkpointing it.
3. Review the dirty tree and decide whether to stage/commit the public plugin bundle, document ingest, Obsidian export/import, company-brain flows, and trust gate together or split into commits.
4. Prepare the public-facing article/comparison after the implementation is committed.
5. If publishing, verify packaging includes untracked modules/docs intended for distribution.

## User Preference

The user expects an actual Hermes handoff, not only a markdown file. Report the exact Hermes session name/ID back to the user.
