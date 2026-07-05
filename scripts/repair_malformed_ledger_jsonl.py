#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class LedgerRecord:
    event: dict[str, Any]
    start_line: int
    end_line: int


class RepairError(RuntimeError):
    pass


def _parse_records(ledger_path: Path) -> tuple[list[LedgerRecord], list[dict[str, Any]], int]:
    physical_lines = ledger_path.read_text(encoding="utf-8").splitlines()
    records: list[LedgerRecord] = []
    repaired_blocks: list[dict[str, Any]] = []
    pending: list[str] = []
    start_line = 0
    last_error: json.JSONDecodeError | None = None

    for line_no, line in enumerate(physical_lines, start=1):
        if not pending:
            start_line = line_no
        pending.append(line)
        candidate = "\\n".join(pending)
        try:
            event = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(event, dict):
            raise RepairError(f"JSONL record at physical lines {start_line}-{line_no} is not an object")
        records.append(LedgerRecord(event=event, start_line=start_line, end_line=line_no))
        if len(pending) > 1:
            repaired_blocks.append(
                {
                    "startLine": start_line,
                    "endLine": line_no,
                    "physicalLineCount": len(pending),
                    "eventId": event.get("event_id"),
                }
            )
        pending = []
        last_error = None

    if pending:
        detail = f"{last_error.msg} at column {last_error.colno}" if last_error else "unknown parse error"
        raise RepairError(f"Could not reconstruct JSONL record starting at physical line {start_line}: {detail}")

    return records, repaired_blocks, len(physical_lines)


def _canonicalize_records(records: list[LedgerRecord]) -> tuple[list[str], list[dict[str, Any]]]:
    hash_repairs: list[dict[str, Any]] = []
    previous_hash: str | None = None
    output_lines: list[str] = []

    for index, record in enumerate(records, start=1):
        event = dict(record.event)
        event_id = event.get("event_id")
        if event.get("prev_hash") != previous_hash:
            raise RepairError(
                "Ledger prev_hash mismatch at logical record "
                f"{index} physical lines {record.start_line}-{record.end_line}"
            )

        computed_hash = sha256_json({k: v for k, v in event.items() if k != "hash"})
        stored_hash = event.get("hash")
        if stored_hash != computed_hash:
            if index != len(records):
                raise RepairError(
                    "Refusing to rewrite a non-final event hash without a full migration: "
                    f"logical record {index} event_id={event_id}"
                )
            event["hash"] = computed_hash
            hash_repairs.append(
                {
                    "logicalRecord": index,
                    "startLine": record.start_line,
                    "endLine": record.end_line,
                    "eventId": event_id,
                    "oldHashPrefix": str(stored_hash or "")[:16],
                    "newHashPrefix": computed_hash[:16],
                }
            )

        output_lines.append(canonical_json(event))
        previous_hash = str(event.get("hash") or "")

    return output_lines, hash_repairs


def repair_ledger(ledger_path: Path, *, apply: bool) -> dict[str, Any]:
    ledger_path = ledger_path.expanduser().resolve()
    if not ledger_path.exists():
        raise RepairError(f"Ledger does not exist: {ledger_path}")

    records, repaired_blocks, physical_line_count = _parse_records(ledger_path)
    output_lines, hash_repairs = _canonicalize_records(records)
    original_text = ledger_path.read_text(encoding="utf-8")
    repaired_text = "\n".join(output_lines) + ("\n" if output_lines else "")
    changed = repaired_text != original_text
    backup_path = None

    if apply and changed:
        backup_path = ledger_path.with_name(f"{ledger_path.name}.pre-jsonl-repair-{utc_stamp()}.bak")
        shutil.copy2(ledger_path, backup_path)
        ledger_path.write_text(repaired_text, encoding="utf-8")

    return {
        "ok": True,
        "applied": bool(apply and changed),
        "changed": changed,
        "ledger": str(ledger_path),
        "backup": str(backup_path) if backup_path else None,
        "physicalLineCountBefore": physical_line_count,
        "logicalEventCount": len(records),
        "repairedBlocks": repaired_blocks,
        "hashRepairs": hash_repairs,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely repair malformed Total Recall JSONL ledger records.")
    parser.add_argument("--home", default="", help="Total Recall home. Uses <home>/ledger/events.jsonl.")
    parser.add_argument("--ledger", default="", help="Explicit ledger/events.jsonl path.")
    parser.add_argument("--apply", action="store_true", help="Write the repaired ledger after creating a timestamped backup.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if bool(args.home) == bool(args.ledger):
        print(json.dumps({"ok": False, "error": "provide exactly one of --home or --ledger"}, indent=2), file=sys.stderr)
        return 2
    ledger_path = Path(args.ledger).expanduser() if args.ledger else Path(args.home).expanduser() / "ledger" / "events.jsonl"
    try:
        result = repair_ledger(ledger_path, apply=args.apply)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
