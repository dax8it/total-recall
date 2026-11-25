"""Bounded automatic recall. Explicit verification never consults this path."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .api import RESUME_PACKET_SCHEMA_VERSION, TotalRecallConfig, _safe_id, sha256_json
from .automatic_index import AutomaticIndex, INLINE_BUILD_BYTES, fingerprint, ledger_position


@dataclass
class Recall:
    rows: list
    verification: dict
    failure: str = ""
    packet: str = ""
    catching_up: bool = False


class AutomaticRecall:
    def __init__(self):
        self._selected = None

    def _checkpoint(self, core, session_id):
        directory = core.home / "checkpoints"
        key = (directory, fingerprint(directory.stat()), session_id)
        if self._selected is None or key != self._selected[0]:
            self._selected = (key, core._select_checkpoint(session_id))
        return self._selected[1]

    @staticmethod
    def _event(index, ledger, row):
        ledger.seek(row["ledger_offset"])
        event = index.checked_event(ledger.readline(), offset=row["ledger_offset"])
        if event["hash"] != row["hash"]:
            raise ValueError("indexed event no longer matches ledger")
        return event

    def _signed_prefix(self, index, conn, ledger, cursor, session_id):
        core = index.core
        path = self._checkpoint(core, session_id)
        if path is None:
            return {}, "checkpoint_not_found"
        try:
            checkpoint = core._read_json(path)
            digest = checkpoint.get("checkpoint_hash")
            if checkpoint.get("checkpoint_id") != path.stem or digest != sha256_json({
                key: value for key, value in checkpoint.items() if key != "checkpoint_hash"
            }):
                return {}, "checkpoint_hash_mismatch"
            count = checkpoint.get("event_count")
            if type(count) is not int or not 0 <= count <= cursor["count"]:
                return {}, "checkpoint_event_count_invalid"
            row = conn.execute("SELECT hash,ledger_offset FROM documents WHERE id=?", (count,)).fetchone() if count else None
            head = self._event(index, ledger, row)["hash"] if row else None
            if head != checkpoint.get("last_event_hash"):
                return {}, "last_event_hash_mismatch"
            anchor_path = core.home / "anchors" / f"{path.stem}.json"
            if not anchor_path.exists():
                return {}, "anchor_not_found"
            anchor = core._read_json(anchor_path)
            if anchor.get("checkpoint_id") != path.stem or anchor.get("checkpoint_hash") != digest:
                return {}, "anchor_checkpoint_hash_mismatch"
            if not core._verify_anchor_signature(anchor, create_key=False):
                return {}, "anchor_signature_mismatch"
            return {"checkpointFile": str(path), "anchorFile": str(anchor_path), "count": count}, ""
        except (OSError, ValueError, AttributeError, TypeError, KeyError):
            return {}, "checkpoint_or_anchor_unreadable"

    def lookup(self, core, query, *, session_id, resume_session, deadline, session_only=False):
        index = AutomaticIndex(core)
        args = (query, session_id, resume_session, deadline, session_only)
        try:
            with core._locked(shared=True, blocking=False):
                position = ledger_position(core)
                cursor = index.cursor(position)
                if cursor and cursor["offset"] == position.size:
                    return self._indexed(index, position, cursor, *args)
            if cursor is None and position.size <= INLINE_BUILD_BYTES:
                index.rebuild(deadline=deadline)
            elif cursor is not None:
                with core._locked(blocking=False):
                    position = ledger_position(core)
                    cursor = index.cursor(position)
                    if cursor is not None:
                        index.advance_locked(position, cursor, deadline=min(deadline, time.monotonic() + 0.3))
            with core._locked(shared=True, blocking=False):
                position = ledger_position(core)
                cursor = index.cursor(position)
                if cursor:
                    return self._indexed(index, position, cursor, *args)
        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
            if isinstance(exc, TimeoutError) and time.monotonic() >= deadline:
                raise
            # Keep diagnostics bounded and never put arbitrary SQLite errors
            # or ledger data into the prompt.
            if isinstance(exc, BlockingIOError):
                failure = "ledger_busy"
            elif isinstance(exc, ValueError):
                failure = "ledger_or_state_invalid"
                if str(exc).startswith(("ledger hash mismatch", "ledger prev_hash mismatch")):
                    failure += f": {exc}"
            else:
                failure = "automatic_index_unavailable"
        else:
            failure = "automatic_index_catching_up"
        return self._legacy(index, *args, failure)

    def _indexed(self, index, position, cursor, query, session_id, resume_session, deadline, session_only):
        core = index.core
        with closing(index.open_read(index.path)) as conn, core.ledger_file.open("rb") as ledger:
            verification, failure = self._signed_prefix(index, conn, ledger, cursor, session_id) if not session_only else ({}, "")
            pending = cursor["offset"] < position.size
            if pending:
                verification, failure = {}, "automatic_index_catching_up"
            results = index.search(conn, query, session_id=session_id, minimum=3, deadline=deadline, session_only=session_only)
            rows = []
            signed_count = verification.get("count", 0)
            for row in results:
                event = self._event(index, ledger, row)
                if event.get("scope") not in core.config.allowed_scopes:
                    continue
                if event.get("text"):
                    rows.append((row["id"] <= signed_count, event["event_id"], event["text"]))
            packet = self._resume(index, conn, ledger, resume_session, verification, deadline) if resume_session else ""
            if fingerprint(os.fstat(ledger.fileno())) != position.file or ledger_position(core).file != position.file:
                raise ValueError("ledger changed during automatic recall")
            if index.cursor(position) != cursor:
                raise ValueError("automatic index changed during recall")
        return Recall(rows, verification, failure, packet, pending)

    def _legacy(self, index, query, session_id, resume_session, deadline, session_only, failure):
        # Older stores already have this FTS projection. It is useful while the
        # new cursor is built, but it cannot attest to the current chain.
        try:
            with closing(index.open_read(index.core.index_file)) as conn:
                results = index.search(conn, query, session_id=session_id, minimum=3, deadline=deadline, session_only=session_only)
                packet = self._resume(index, conn, None, resume_session, {}, deadline) if resume_session else ""
                rows = [(False, row["item_id"], row["text"]) for row in results if row["text"]]
                return Recall(rows, {}, failure, packet, True)
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            return Recall([], {}, failure, catching_up=True)

    def _resume(self, index, conn, ledger, session_id, verification, deadline):
        core = index.core
        directory = core.home / "continuation" / _safe_id(session_id)
        try:
            with os.scandir(directory) as entries:
                packets = []
                for entry in entries:
                    if time.monotonic() >= deadline:
                        return ""
                    if entry.name.startswith("packet_") and entry.name.endswith(".json") and entry.is_file():
                        packets.append((entry.stat().st_mtime_ns, Path(entry.path)))
        except OSError:
            return ""
        for _, path in sorted(packets, reverse=True):
            if time.monotonic() >= deadline:
                return ""
            try:
                packet = core._read_json(path)
                if packet.get("schema") != RESUME_PACKET_SCHEMA_VERSION or packet.get("session_id") != session_id:
                    continue
                info = packet.get("ledger") or {}
                head = info.get("last_event_hash")
                linked = None
                if ledger is not None:
                    linked = conn.execute("SELECT id FROM documents WHERE hash=?", (head,)).fetchone()
                    if not linked or linked["id"] != info.get("event_count"):
                        continue
                turns = (packet.get("recent_turns") or [])[-30:]
                if ledger is not None and linked is not None:
                    allowed = {}
                    for turn in turns:
                        row = conn.execute("SELECT * FROM documents WHERE hash=?", (turn.get("hash"),)).fetchone()
                        if row and row["session_id"] == session_id and row["scope"] in core.config.allowed_scopes:
                            event = self._event(index, ledger, row)
                            if row["id"] <= linked["id"] and event.get("kind") == "turn":
                                allowed[event["event_id"]] = (event.get("text"), event.get("hash"))
                elif turns:
                    placeholders = ",".join("?" for _ in turns)
                    # Legacy documents do not have ledger hashes or an item-id
                    # index. One indexed session lookup is sufficient here.
                    legacy_rows = conn.execute(f"SELECT item_id,text,scope FROM documents WHERE session_id=? "
                                               f"AND item_id IN ({placeholders})",
                                               (session_id, *(turn.get("event_id") for turn in turns)))
                    allowed = {row["item_id"]: (row["text"], None) for row in legacy_rows
                               if row["scope"] in core.config.allowed_scopes}
                else:
                    allowed = {}
                turns = [turn for turn in turns if turn.get("event_id") in allowed and
                         allowed[turn["event_id"]][0] == turn.get("text") and
                         (ledger is None or allowed[turn["event_id"]][1] == turn.get("hash"))]
                # The packet is a derived file, not signed content. Correlate
                # its verbatim turns and rederive actions from allowed inputs.
                if core.config.allowed_scopes != TotalRecallConfig().allowed_scopes:
                    packet = {**packet, "open_loops": [], "freshness": {}, "compiled_truth_excerpt": ""}
                packet = {**packet, "recent_turns": [turn for turn in turns[-3:] if len(turn.get("text") or "") < 650],
                          "next_actions": core._extract_next_actions(recent_turns=turns,
                                                                     open_loops=packet.get("open_loops") or [])}
                return core._resume_context_block(packet, verification=verification, packet_file=str(path),
                                                  char_budget=1800, status="UNVERIFIED")
            except (OSError, ValueError, AttributeError, TypeError, KeyError):
                continue
        return ""
