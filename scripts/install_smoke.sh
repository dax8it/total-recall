#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR="${TMPDIR:-/tmp}"
WORKDIR="$(mktemp -d "$TMPDIR/total-recall-install-smoke.XXXXXX")"
PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "No Python >=3.11 interpreter found." >&2
  exit 1
fi
cleanup() {
  rm -rf "$WORKDIR"
}
trap cleanup EXIT
if command -v uv >/dev/null 2>&1; then
  uv venv -p "$PYTHON_BIN" "$WORKDIR/venv" >/dev/null
  uv pip install --python "$WORKDIR/venv/bin/python" "$ROOT" >/dev/null
else
  "$PYTHON_BIN" -m venv "$WORKDIR/venv"
  "$WORKDIR/venv/bin/python" -m pip install --upgrade pip >/dev/null
  "$WORKDIR/venv/bin/python" -m pip install "$ROOT" >/dev/null
fi
export TOTAL_RECALL_HOME="$WORKDIR/store"
"$WORKDIR/venv/bin/total-recall" health >/dev/null
"$WORKDIR/venv/bin/total-recall" ingest --kind note --text "Install smoke continuity memory." --session-id install-smoke >/dev/null
mkdir -p "$WORKDIR/docs"
printf 'Install smoke document context for onboarding.' > "$WORKDIR/docs/context.md"
"$WORKDIR/venv/bin/total-recall" documents ingest "$WORKDIR/docs" --session-id install-docs --dry-run >/dev/null
"$WORKDIR/venv/bin/total-recall" documents ingest "$WORKDIR/docs" --session-id install-docs >/dev/null
"$WORKDIR/venv/bin/total-recall" sources ingest --type meeting --title "Install Smoke Review" --occurred-at 2026-01-01T00:00:00Z --scope public --text "Decision: Install smoke promise is day-one memory continuity." >/dev/null
"$WORKDIR/venv/bin/total-recall" search "smoke continuity" >/dev/null
"$WORKDIR/venv/bin/total-recall" search "document context" --session-id install-docs >/dev/null
"$WORKDIR/venv/bin/total-recall" knowledge freshness --entity "install smoke promise" --category promise --format text >/dev/null
"$WORKDIR/venv/bin/total-recall" knowledge graph timeline --entity "install smoke promise" --at-time 2026-01-02T00:00:00Z >/dev/null
"$WORKDIR/venv/bin/total-recall" vault export --out "$WORKDIR/obsidian-vault" >/dev/null
printf '%s\n' '---' 'type: "edited_note"' '---' '# Install Smoke Edited' '' 'Decision: Install smoke edited note import is owner reviewed.' > "$WORKDIR/obsidian-vault/Install Smoke Edited.md"
PREVIEW_ID="$("$WORKDIR/venv/bin/total-recall" vault import-preview --vault "$WORKDIR/obsidian-vault" --note "Install Smoke Edited.md" --format json | "$WORKDIR/venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["preview_id"])')"
"$WORKDIR/venv/bin/total-recall" vault import-promote "$PREVIEW_ID" >/dev/null
"$WORKDIR/venv/bin/total-recall" learning review --session-id install-learning --format text >/dev/null
TOTAL_RECALL_HOME="$WORKDIR/federated" "$WORKDIR/venv/bin/total-recall" ingest --kind note --text "Federated install smoke promise is workspace-separated." --session-id fed --scope public >/dev/null
TOTAL_RECALL_HOME="$WORKDIR/federated" "$WORKDIR/venv/bin/total-recall" knowledge index rebuild >/dev/null
"$WORKDIR/venv/bin/total-recall" federation register smoke-agent "$WORKDIR/federated" --scope public >/dev/null
"$WORKDIR/venv/bin/total-recall" federation query --query "federated install smoke promise" --target smoke-agent --authorize >/dev/null
"$WORKDIR/venv/bin/total-recall" checkpoint --session-id install-smoke >/dev/null
"$WORKDIR/venv/bin/total-recall" verify --session-id install-smoke >/dev/null
"$WORKDIR/venv/bin/total-recall" trust verify --format text >/dev/null
"$WORKDIR/venv/bin/total-recall" rehydrate --session-id install-smoke --query "smoke continuity" >/dev/null
"$WORKDIR/venv/bin/total-recall" doctor >/dev/null
"$WORKDIR/venv/bin/total-recall" export --out "$WORKDIR/recall.tar.gz" >/dev/null
TOTAL_RECALL_HOME="$WORKDIR/imported" "$WORKDIR/venv/bin/total-recall" import "$WORKDIR/recall.tar.gz" >/dev/null
TOTAL_RECALL_HOME="$WORKDIR/imported" "$WORKDIR/venv/bin/total-recall" verify --session-id install-smoke >/dev/null
"$WORKDIR/venv/bin/total-recall" backup run --out-dir "$WORKDIR/backups" --keep 1 --keep-days 365 >/dev/null
"$WORKDIR/venv/bin/total-recall" backup status --out-dir "$WORKDIR/backups" >/dev/null
"$WORKDIR/venv/bin/total-recall" hermes install --hermes-home "$WORKDIR/hermes" --force --core-install skip >/dev/null
"$WORKDIR/venv/bin/total-recall" hermes status --hermes-home "$WORKDIR/hermes" --skip-core-check >/dev/null
"$WORKDIR/venv/bin/total-recall" hermes bundle --out "$WORKDIR/total-recall-hermes-plugin.tar.gz" >/dev/null
echo "Install smoke passed."
