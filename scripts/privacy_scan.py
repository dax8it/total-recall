#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build"}
SKIP_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".sqlite", ".db"}
PATTERNS = [
    re.compile(r"/Users/[A-Za-z0-9_.-]+"),
    re.compile(r"\\.openclaw", re.IGNORECASE),
    re.compile(r"fattyclaw", re.IGNORECASE),
    re.compile(r"OPENAI_API_KEY"),
    re.compile(r"ANTHROPIC_API_KEY"),
    re.compile(r"(?:password|credential)\s*[:=]", re.IGNORECASE),
]


def iter_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.relative_to(ROOT).as_posix() == "scripts/privacy_scan.py":
            continue
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        files.append(path)
    return files


def main() -> int:
    findings: list[str] = []
    for path in iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in PATTERNS:
                if pattern.search(line):
                    rel = path.relative_to(ROOT)
                    findings.append(f"{rel}:{lineno}: matched {pattern.pattern}")
                    break
    if findings:
        print("Privacy scan failed:")
        print("\n".join(findings))
        return 1
    print("Privacy scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
