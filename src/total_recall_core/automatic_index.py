"""Disposable FTS projection and checked byte cursor for Hermes auto recall.

The full verifier does not use these files. All normal ledger appends record
which file generation they extended under the ledger lock. An unknown edit,
replacement, interrupted write, or missing seal requires a new chain scan;
file growth alone is not evidence that the previously checked bytes survived.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .api import _ledger_line_matches_hash, canonical_json, sha256_json
from .turn_capture import tool_search_terms

# Keep the ledger lock for at most one small batch during ordinary catch-up.
BATCH_BYTES = 2 * 1024 * 1024
INLINE_BUILD_BYTES = 512 * 1024


class InvalidLedger(ValueError):
    """The same file fingerprint cannot complete a projection on retry."""


def fingerprint(stat):
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def _read_json(path):
    try:
        value = json.loads(path.read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@dataclass(frozen=True)
class Position:
    file: list[int]
    generation: list[int]
    tracked: bool = False
    head: str | None = None

    @property
    def size(self):
        return self.file[2]


def ledger_position(core):
    current = fingerprint(core.ledger_file.stat())
    witness = _read_json(core.home / "automatic-recall" / "ledger-position.json")
    if witness.get("file") == current and isinstance(witness.get("generation"), list):
        return Position(current, witness["generation"], True, witness.get("head"))
    return Position(current, current)


def append_events(core, before, events):
    # Caller holds the exclusive ledger lock. Publish the witness only after
    # the append completes. Losing this derived metadata never loses an event.
    with core.ledger_file.open("a", encoding="utf-8") as ledger:
        for event in events:
            ledger.write(canonical_json(event) + "\n")
    try:
        core._write_json(core.home / "automatic-recall" / "ledger-position.json", {
            "file": fingerprint(core.ledger_file.stat()),
            "generation": before.generation,
            "head": events[-1]["hash"],
        })
    except OSError:
        pass


class AutomaticIndex:
    def __init__(self, core):
        self.core = core
        self.path = core.home / "automatic-recall" / "fts-v1.sqlite"
        self.seal = self.path.with_suffix(".json")

    def _publish(self, position, offset, count, head):
        self.core._write_json(self.seal, {
            "index": fingerprint(self.path.stat()), "generation": position.generation,
            "offset": offset, "count": count, "head": head,
        })

    def cursor(self, position):
        stamp = _read_json(self.seal)
        try:
            if (stamp.get("generation") == position.generation
                    and stamp.get("index") == fingerprint(self.path.stat())
                    and 0 <= stamp["offset"] <= position.size):
                return stamp
        except (OSError, KeyError, TypeError):
            pass
        return None

    @staticmethod
    def open_read(path):
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _create(conn):
        conn.executescript("""
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, timestamp TEXT,
                session_id TEXT NOT NULL, scope TEXT NOT NULL, text TEXT NOT NULL,
                tool_terms TEXT NOT NULL, hash TEXT NOT NULL, ledger_offset INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX document_hash ON documents(hash);
            CREATE INDEX document_session ON documents(session_id, id);
            CREATE VIRTUAL TABLE documents_fts
                USING fts5(text, tool_terms, content='documents', content_rowid='id');
        """)

    @staticmethod
    def checked_event(line, *, offset):
        try:
            event = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise InvalidLedger(f"invalid ledger JSON at byte {offset}") from exc
        if not isinstance(event, dict):
            raise InvalidLedger(f"ledger record is not an object at byte {offset}")
        head = event.get("hash")
        if not isinstance(head, str) or (not _ledger_line_matches_hash(line, head) and head != sha256_json({
            key: value for key, value in event.items() if key != "hash"
        })):
            raise InvalidLedger(f"ledger hash mismatch at byte {offset}")
        return event

    def _project(self, conn, ledger, *, offset, end, count, head, deadline):
        ledger.seek(offset)
        while offset < end:
            if time.monotonic() >= deadline:
                raise TimeoutError("automatic index catch-up budget expired")
            line = ledger.readline()
            if not line.endswith(b"\n"):
                raise InvalidLedger(f"incomplete ledger record at byte {offset}")
            if line.strip():
                event = self.checked_event(line, offset=offset)
                if event.get("prev_hash") != head:
                    raise InvalidLedger(f"ledger prev_hash mismatch at byte {offset}")
                head = event["hash"]
                count += 1
                text = str(event.get("text") or "")
                terms = tool_search_terms(event)
                conn.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)", (
                    count, event["event_id"], event.get("timestamp"), event.get("session_id") or "default",
                    event.get("scope") or "private", text, terms, head, offset,
                ))
                conn.execute("INSERT INTO documents_fts(rowid,text,tool_terms) VALUES (?,?,?)", (count, text, terms))
            offset += len(line)
        return offset, count, head

    def advance_locked(self, position, cursor, *, deadline):
        if position.size == cursor["offset"]:
            return cursor
        # A whole record may cross the batch limit. The limit is never beyond EOF.
        with closing(sqlite3.connect(self.path, timeout=0)) as conn, self.core.ledger_file.open("rb") as ledger:
            with conn:
                progress = self._project(conn, ledger, offset=cursor["offset"],
                                         end=min(position.size, cursor["offset"] + BATCH_BYTES),
                                         count=cursor["count"], head=cursor["head"], deadline=deadline)
                if fingerprint(os.fstat(ledger.fileno())) != position.file or ledger_position(self.core).file != position.file:
                    raise ValueError("ledger changed during automatic index update")
        self._publish(position, *progress)
        return self.cursor(position)

    def rebuild(self, *, deadline=float("inf")):
        # Build off-lock. Before publishing, require the same file generation;
        # cooperating appenders may have added a suffix since we opened it.
        with self.core._locked(shared=True, blocking=False):
            position = ledger_position(self.core)
            ledger = self.core.ledger_file.open("rb")
            if fingerprint(os.fstat(ledger.fileno())) != position.file:
                ledger.close()
                raise ValueError("ledger changed before automatic index rebuild")
        with ledger, tempfile.TemporaryDirectory(prefix=".automatic-", dir=self.path.parent) as work:
            work_db = Path(work) / self.path.name
            with closing(sqlite3.connect(work_db)) as conn:
                with conn:
                    self._create(conn)
                    progress = self._project(conn, ledger, offset=0, end=position.size,
                                             count=0, head=None, deadline=deadline)
            with self.core._locked(blocking=False):
                current = ledger_position(self.core)
                if current.generation != position.generation or progress[0] != position.size:
                    raise ValueError("ledger changed during automatic index rebuild")
                existing = self.cursor(current)
                if existing and existing["offset"] >= progress[0]:
                    return
                os.replace(work_db, self.path)
                self._publish(position, *progress)

    def search(self, conn, query, *, session_id, minimum, deadline, session_only):
        """Same FTS5/BM25 query as interactive search; bound broad queries.

        When strict AND has no match for a natural-language prompt, rank a
        bounded recent OR candidate set rather than scanning ledger text.
        """
        scopes = tuple(self.core.config.allowed_scopes)
        if not scopes:
            return []
        scope_sql = ",".join("?" for _ in scopes)
        where = f"d.scope IN ({scope_sql})"
        params = scopes
        if session_only:
            where += " AND d.session_id=?"
            params += (session_id,)
        # Bound common terms without replacing relevance with unrelated recent
        # events. A timed-out BM25 sort falls back to recent *matching* rows.
        def match(expr, *, bounded):
            stop = min(deadline, time.monotonic() + (0.04 if bounded else 0.09))
            conn.set_progress_handler(lambda: time.monotonic() >= stop, 1000)
            order = "documents_fts.rowid DESC LIMIT 96" if bounded else "score DESC, d.timestamp DESC LIMIT 8"
            try:
                rows = conn.execute("SELECT d.id, -bm25(documents_fts) AS score FROM documents_fts "
                                    "JOIN documents d ON d.id=documents_fts.rowid WHERE documents_fts MATCH ? AND "
                                    + where + " ORDER BY " + order, (expr, *params)).fetchall()
                return sorted(rows, key=lambda row: (row["score"], row["id"]), reverse=True)[:8] if bounded else rows
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_INTERRUPT:
                    raise
                return None
            finally:
                conn.set_progress_handler(None, 0)

        terms = list(dict.fromkeys(self.core._fts_tokens(query)))[:24]
        quoted = [f'"{term}"' for term in terms if any(c.isalnum() for c in term)]
        rows = []
        for expr in dict.fromkeys((" ".join(quoted), " OR ".join(quoted))):
            if not expr:
                continue
            ranked = match(expr, bounded=False)
            if ranked is None:
                ranked = match(expr, bounded=True)
            if ranked:
                rows = [conn.execute("SELECT * FROM documents WHERE id=?", (row["id"],)).fetchone() for row in ranked]
                break
        # A current turn should be able to retrieve its own recent events even
        # before the next signed checkpoint. Never reserve its space by age.
        if len(rows) < minimum:
            stop = min(deadline, time.monotonic() + 0.06)
            conn.set_progress_handler(lambda: time.monotonic() >= stop, 1000)
            try:
                base = "SELECT d.* FROM documents d WHERE " + where
                recent = conn.execute(base + ("" if session_only else " AND d.session_id=?") +
                                      " ORDER BY d.id DESC LIMIT ?", (*scopes, session_id, minimum)).fetchall()
                if len(recent) < minimum and not session_only:
                    recent += conn.execute(base + " ORDER BY d.id DESC LIMIT ?",
                                           (*scopes, minimum)).fetchall()
                seen = {row["id"] for row in rows}
                for row in recent:
                    if row["id"] not in seen and row["text"].strip():
                        seen.add(row["id"])
                        rows.append(row)
                    if len(rows) >= minimum:
                        break
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", None) != sqlite3.SQLITE_INTERRUPT:
                    raise
            finally:
                conn.set_progress_handler(None, 0)
        return rows
