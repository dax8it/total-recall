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
"$WORKDIR/venv/bin/total-recall" search "smoke continuity" >/dev/null
"$WORKDIR/venv/bin/total-recall" checkpoint --session-id install-smoke >/dev/null
"$WORKDIR/venv/bin/total-recall" verify --session-id install-smoke >/dev/null
"$WORKDIR/venv/bin/total-recall" rehydrate --session-id install-smoke --query "smoke continuity" >/dev/null
"$WORKDIR/venv/bin/total-recall" doctor >/dev/null
"$WORKDIR/venv/bin/total-recall" export --out "$WORKDIR/recall.tar.gz" >/dev/null
TOTAL_RECALL_HOME="$WORKDIR/imported" "$WORKDIR/venv/bin/total-recall" import "$WORKDIR/recall.tar.gz" >/dev/null
TOTAL_RECALL_HOME="$WORKDIR/imported" "$WORKDIR/venv/bin/total-recall" verify --session-id install-smoke >/dev/null
echo "Install smoke passed."
