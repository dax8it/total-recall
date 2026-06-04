#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3.13 python3.12 python3.11 python3; do
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

"$PYTHON_BIN" -m pip install "$ROOT"
"$PYTHON_BIN" -m total_recall_core.cli hermes install --force "$@"

cat <<'MSG'

Total Recall Hermes plugin install command completed.

The installer checks Hermes' Python environment and installs total-recall-core
there unless you pass --core-install skip.

If you did not pass --profile <profile> --activate, select it with:
  hermes plugins enable total-recall
  hermes -p <profile> config set memory.provider total-recall
  hermes -p <profile> memory status

Check readiness with:
  total-recall hermes doctor
MSG
