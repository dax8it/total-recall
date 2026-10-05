#!/usr/bin/env python3
"""Append missing Hermes message records from an operator-preserved snapshot.

No inferred user/assistant pairing, backdated ledger timestamps, or store reset.
Run plan first; apply rechecks coverage under Total Recall's cooperative lock.
The JSON snapshot must contain only the authorized profile's selected records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path

from total_recall_core import TotalRecallConfig, TotalRecallCore
from total_recall_core.api import canonical_json, sha256_json, utc_now


def record_key(record, source):
    return sha256_json({"source": source, "session_id": record["session_id"],
                        "message_id": record["id"], "role": record["role"],
                        "content": record["content"]})


def plan_records(records, events, source):
    captured = defaultdict(list)
    recovered_keys = set()
    for event in events:
        if event.get("kind") == "turn" and event.get("source", "hermes.sync_turn") == "hermes.sync_turn":
            captured[event.get("session_id")].append(event.get("text", ""))
        recovered_keys.update((event.get("metadata") or {}).get("recovery_record_keys", []))
    counts = {"source": len(records), "excluded": 0, "covered": 0, "pending": 0}
    pending = []
    seen = set(recovered_keys)
    for record in records:
        role = record.get("role")
        content = record.get("content")
        allowed = (role == "user" and record.get("display_kind") in (None, "steer")) or (
            role == "assistant" and record.get("display_kind") is None
            and record.get("finish_reason") in (None, "stop") and not record.get("tool_calls"))
        if not allowed or record.get("_compressed_summary") or not isinstance(content, str) or not content.strip():
            counts["excluded"] += 1
            continue
        key = record_key(record, source)
        text = content.strip()
        represented = any(
            event_text.startswith(f"User: {text}\nAssistant: ") if role == "user"
            else event_text.endswith(f"\nAssistant: {text}")
            for event_text in captured[record["session_id"]]
        )
        if key in seen or represented:
            counts["covered"] += 1
        else:
            pending.append(record)
            counts["pending"] += 1
        seen.add(key)
    return {"records": pending, "counts": counts}


def apply_records(core, records, source, snapshot_sha256):
    with core._locked():
        existing = core._read_events(verify_chain=True)
        plan = plan_records(records, existing, source)
        grouped = defaultdict(list)
        for record in plan["records"]:
            grouped[record["session_id"]].append(record)
        previous_hash = existing[-1]["hash"] if existing else None
        prepared = []
        for session_id, group in grouped.items():
            group.sort(key=lambda record: record["id"])
            base = {
                "event_id": core._new_id("evt_recovered"), "timestamp": utc_now(),
                "kind": "recovered_session_records", "session_id": session_id,
                "scope": "private", "source": "hermes.session_recovery",
                "text": "Recovered original Hermes message records (not inferred turns). "
                        "Record timestamps describe source storage; ledger timestamp is recovery time.\n"
                        + "\n".join(canonical_json(record) for record in group),
                "metadata": {
                    "schema": "hermes-session-gap-recovery-v1", "source_database": source,
                    "source_snapshot_sha256": snapshot_sha256,
                    "source_message_ids": [record["id"] for record in group],
                    "recovery_record_keys": [record_key(record, source) for record in group],
                    "original_record_timestamps": [record["timestamp"] for record in group],
                    "provenance": "Original database content; no inferred pairing or outcome."
                },
                "origin": core._event_origin(source="hermes.session_recovery"),
                "prev_hash": previous_hash,
            }
            event = {**base, "hash": sha256_json(base)}
            prepared.append(event)
            previous_hash = event["hash"]
        if prepared:
            # Same append/reduce/index pattern as ingest_documents, with the coverage
            # decision made inside the same lock as the append to prevent replay races.
            with core.ledger_file.open("a", encoding="utf-8") as ledger:
                ledger.write("".join(canonical_json(event) + "\n" for event in prepared))
                ledger.flush()
                os.fsync(ledger.fileno())
            state = core.reduce_state(write=True)
            core._rebuild_index_locked(state=state, backends=("sqlite-fts",))
        return {"counts": plan["counts"], "recovered_records": len(plan["records"]),
                "appended_events": len(prepared), "event_ids": [e["event_id"] for e in prepared]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    raw = args.snapshot.read_bytes()
    snapshot = json.loads(raw)
    core = TotalRecallCore(TotalRecallConfig(home=args.home, enable_qmd=False, enable_lancedb=False))
    if args.apply:
        result = apply_records(core, snapshot["records"], snapshot["source_database"], hashlib.sha256(raw).hexdigest())
    else:
        with core._locked(shared=True):
            result = plan_records(snapshot["records"], core._read_events(verify_chain=True), snapshot["source_database"])
        result = {"counts": result["counts"], "sessions": len({r["session_id"] for r in result["records"]})}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
