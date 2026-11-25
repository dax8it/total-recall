"""Read-only Hermes capture evidence, usable without importing the provider or core."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


STALE_SECONDS = 12 * 60 * 60
LOOKBACK_SECONDS = 30 * 24 * 60 * 60


def utc_date(timestamp: float | None) -> str:
    if timestamp is None:
        return "none found"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass(frozen=True)
class CaptureGap:
    last_capture: float | None
    first_reply: float
    last_reply: float

    def dates(self) -> str:
        return (f"Last captured turn: {utc_date(self.last_capture)}. "
                f"Hermes replies without capture progress: {utc_date(self.first_reply)} to {utc_date(self.last_reply)}.")


def _reverse_complete_lines(path: Path):
    """Read the ledger tail in blocks, ignoring an append that has not reached LF."""
    with path.open("rb") as stream:
        offset = stream.seek(0, 2)
        remainder = b""
        at_end = True
        while offset:
            size = min(offset, 64 * 1024)
            offset -= size
            stream.seek(offset)
            remainder, *lines = (stream.read(size) + remainder).split(b"\n")
            if at_end and lines:
                lines.pop()
                at_end = False
            yield from reversed(lines)
        if not at_end:
            yield remainder


def last_capture_time(store: Path) -> float | None:
    try:
        for line in _reverse_complete_lines(store / "ledger" / "events.jsonl"):
            if b'hermes.sync_turn' not in line:
                continue
            try:
                event = json.loads(line)
                if (isinstance(event, dict) and event.get("kind") == "turn"
                        and event.get("source") == "hermes.sync_turn"):
                    return datetime.fromisoformat(event["timestamp"]).timestamp()
            except (ValueError, TypeError, KeyError, RecursionError):
                continue
    except FileNotFoundError:
        pass
    return None


def capture_gap(profile: Path, store: Path, *, now: float) -> CaptureGap | None:
    last = last_capture_time(store)
    if last is not None and last > now - STALE_SECONDS:
        return None
    db = profile / "state.db"
    try:
        db.stat()
    except FileNotFoundError:
        return None
    # The ledger's clock has whole-second precision; Hermes SQLite does not.
    # A reply written earlier in the same second can already be captured.
    since = max(last + 1 if last is not None else 0, now - LOOKBACK_SECONDS)
    # Drive the time-range lookup from sessions so Hermes' (session_id,
    # timestamp) index can skip old rows even in an 8 GB profile. No transcript
    # text leaves this query. Compacted original replies still count.
    sql = """
        SELECT min(m.timestamp), max(m.timestamp)
        FROM sessions AS s CROSS JOIN messages AS m
        WHERE m.session_id = s.id AND m.timestamp > ? AND m.timestamp <= ?
          AND s.source NOT IN ('cron', 'subagent', 'tool')
          AND m.role = 'assistant' AND m.display_kind IS NULL
          AND m._compressed_summary = 0 AND m.tool_calls IS NULL
          AND (m.finish_reason IS NULL OR m.finish_reason IN ('stop', 'length'))
          AND length(m.content) > 0
    """
    with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as conn:
        conn.execute("PRAGMA query_only=ON")
        first, latest = conn.execute(sql, (since, now)).fetchone()
    if first is not None and first <= now - STALE_SECONDS:
        return CaptureGap(last, first, latest)
    return None
