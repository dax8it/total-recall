from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


VERSION = "1.3.0"
SIGNING_ALGORITHM = "ed25519-local-v1"
LEGACY_SIGNING_ALGORITHM = "hmac-sha256-local-v1"
INDEX_SCHEMA_VERSION = "total-recall-sqlite-fts-v1"
LANCEDB_INDEX_SCHEMA_VERSION = "total-recall-lancedb-derived-v1"
QMD_INDEX_SCHEMA_VERSION = "total-recall-qmd-derived-v1"
EMBEDDING_DIMENSIONS = 128


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _safe_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip())
    return cleaned.strip("._") or "default"


def resolve_default_home() -> Path:
    if os.getenv("TOTAL_RECALL_HOME"):
        return Path(os.environ["TOTAL_RECALL_HOME"]).expanduser()
    if os.getenv("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"]).expanduser() / "total-recall"
    return Path.home() / ".total-recall"


@dataclass(frozen=True)
class TotalRecallConfig:
    home: Path = field(default_factory=resolve_default_home)
    allowed_scopes: tuple[str, ...] = field(
        default_factory=lambda: ("private", "group_safe", "internal", "shared_team")
    )
    workspace_root: Optional[Path] = None
    enable_lancedb: bool = field(
        default_factory=lambda: os.getenv("TOTAL_RECALL_ENABLE_LANCEDB", "1") != "0"
    )
    enable_qmd: bool = field(default_factory=lambda: os.getenv("TOTAL_RECALL_ENABLE_QMD", "1") != "0")
    qmd_bin: str = field(default_factory=lambda: os.getenv("TOTAL_RECALL_QMD_BIN", ""))
    qmd_embed: bool = field(default_factory=lambda: os.getenv("TOTAL_RECALL_QMD_EMBED", "0") == "1")

    def __post_init__(self) -> None:
        if self.workspace_root and str(self.home) == ".":
            object.__setattr__(self, "home", self.workspace_root / ".total-recall")


class TotalRecallCore:
    """Framework-neutral Total Recall continuity engine.

    The core owns all durable continuity state. Hermes and other hosts should
    call this API instead of reimplementing checkpoint, anchor, incident,
    external-memory, or rehydrate behavior.
    """

    def __init__(self, config: Optional[TotalRecallConfig] = None) -> None:
        self.config = config or TotalRecallConfig()
        self.home = self.config.home.expanduser().resolve()
        self._ensure_layout()

    @property
    def ledger_file(self) -> Path:
        return self.home / "ledger" / "events.jsonl"

    @property
    def state_file(self) -> Path:
        return self.home / "state" / "current.json"

    @property
    def key_file(self) -> Path:
        return self.home / "keys" / "anchor.key"

    @property
    def ed25519_private_key_file(self) -> Path:
        return self.home / "keys" / "anchor.ed25519"

    @property
    def ed25519_public_key_file(self) -> Path:
        return self.home / "keys" / "anchor.ed25519.pub"

    @property
    def lock_file(self) -> Path:
        return self.home / ".total-recall.lock"

    @property
    def index_file(self) -> Path:
        return self.home / "index" / "total_recall.sqlite"

    @property
    def lancedb_dir(self) -> Path:
        return self.home / "index" / "lancedb"

    @property
    def lancedb_meta_file(self) -> Path:
        return self.home / "index" / "lancedb-meta.json"

    @property
    def qmd_docs_dir(self) -> Path:
        return self.home / "index" / "qmd-docs"

    @property
    def qmd_meta_file(self) -> Path:
        return self.home / "index" / "qmd-meta.json"

    def health(self) -> Dict[str, Any]:
        state = self.reduce_state(write=True)
        latest_checkpoint = self._latest_file(self.home / "checkpoints", "*.json")
        latest_anchor = self._latest_file(self.home / "anchors", "*.json")
        open_incidents = [i for i in self.list_incidents().get("incidents", []) if i.get("status") == "OPEN"]
        index_status = self.index_status(state=state)
        return {
            "ok": True,
            "version": VERSION,
            "home": str(self.home),
            "ledgerFile": str(self.ledger_file),
            "eventCount": state["event_count"],
            "lastEventHash": state.get("last_event_hash"),
            "latestCheckpoint": str(latest_checkpoint) if latest_checkpoint else None,
            "latestAnchor": str(latest_anchor) if latest_anchor else None,
            "index": index_status,
            "openIncidents": len(open_incidents),
            "capabilities": {
                "ledger": "implemented",
                "state": "implemented",
                "checkpoint": "implemented",
                "anchors": "implemented",
                "retrieval": "implemented-lancedb-qmd-sqlite-derived-with-lexical-fallback",
                "rehydrateAuthority": "implemented",
                "incidents": "implemented",
                "externalMemory": "implemented",
                "contextPlanning": "implemented",
            },
        }

    def ingest(
        self,
        *,
        kind: str,
        text: str,
        session_id: str = "default",
        scope: str = "private",
        source: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if scope not in self.config.allowed_scopes:
            raise ValueError(f"scope '{scope}' is not allowed")
        text = str(text or "").strip()
        if not text:
            raise ValueError("text is required")

        with self._locked():
            previous_hash = self._last_event_hash()
            base = {
                "event_id": self._new_id("evt"),
                "timestamp": utc_now(),
                "kind": _safe_id(kind),
                "session_id": session_id or "default",
                "scope": scope,
                "source": source,
                "text": text,
                "metadata": metadata or {},
                "prev_hash": previous_hash,
            }
            event_hash = sha256_json(base)
            event = {**base, "hash": event_hash}
            with self.ledger_file.open("a", encoding="utf-8") as fh:
                fh.write(canonical_json(event) + "\n")
            state = self.reduce_state(write=True)
            self._rebuild_index_locked(state=state, backends=("sqlite-fts",))
        return {"ok": True, "event": event, "stateHash": state["state_hash"]}

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = f"User: {user_content.strip()}\nAssistant: {assistant_content.strip()}".strip()
        return self.ingest(
            kind="turn",
            text=text,
            session_id=session_id,
            source="hermes.sync_turn",
            metadata=metadata or {},
        )

    def reduce_state(self, *, write: bool = False) -> Dict[str, Any]:
        events = self._read_events(verify_chain=True)
        sessions: Dict[str, Dict[str, Any]] = {}
        memories: List[Dict[str, Any]] = []
        promoted_external: List[Dict[str, Any]] = []
        for event in events:
            sid = event.get("session_id") or "default"
            sess = sessions.setdefault(sid, {"event_count": 0, "last_event_hash": None, "updated_at": None})
            sess["event_count"] += 1
            sess["last_event_hash"] = event.get("hash")
            sess["updated_at"] = event.get("timestamp")
            memory = {
                "event_id": event.get("event_id"),
                "timestamp": event.get("timestamp"),
                "kind": event.get("kind"),
                "session_id": sid,
                "scope": event.get("scope"),
                "source": event.get("source"),
                "text": event.get("text"),
                "metadata": event.get("metadata") or {},
                "hash": event.get("hash"),
            }
            memories.append(memory)
            if event.get("kind") == "external_promoted":
                promoted_external.append(memory)

        state = {
            "schema": "total-recall-state-v1",
            "version": VERSION,
            "generated_at": utc_now(),
            "event_count": len(events),
            "last_event_hash": events[-1]["hash"] if events else None,
            "sessions": dict(sorted(sessions.items())),
            "memories": memories,
            "promoted_external": promoted_external,
        }
        state["state_hash"] = sha256_json({k: v for k, v in state.items() if k not in {"generated_at", "state_hash"}})
        if write:
            self._write_json(self.state_file, state)
        return state

    def checkpoint(self, *, session_id: str = "default", label: str = "") -> Dict[str, Any]:
        with self._locked():
            return self._checkpoint_locked(session_id=session_id, label=label)

    def _checkpoint_locked(self, *, session_id: str = "default", label: str = "") -> Dict[str, Any]:
        state = self.reduce_state(write=True)
        created_at = utc_now()
        checkpoint_id = self._new_id(f"checkpoint_{_safe_id(session_id)}")
        checkpoint = {
            "schema": "total-recall-checkpoint-v1",
            "checkpoint_id": checkpoint_id,
            "created_at": created_at,
            "session_id": session_id,
            "label": label,
            "state_hash": state["state_hash"],
            "event_count": state["event_count"],
            "last_event_hash": state.get("last_event_hash"),
            "state_path": str(self.state_file),
            "ledger_path": str(self.ledger_file),
            "home": str(self.home),
            "summary": self._state_summary(state),
        }
        checkpoint["checkpoint_hash"] = sha256_json({k: v for k, v in checkpoint.items() if k != "checkpoint_hash"})
        checkpoint_path = self.home / "checkpoints" / f"{checkpoint_id}.json"
        self._write_json(checkpoint_path, checkpoint)
        anchor = self._write_anchor(checkpoint_path, checkpoint)
        report = self._write_report(
            "checkpoint",
            checkpoint_id,
            {
                "ok": True,
                "checkpoint": checkpoint,
                "checkpointFile": str(checkpoint_path),
                "anchor": anchor,
            },
        )
        return {
            "ok": True,
            "checkpoint": checkpoint,
            "checkpointFile": str(checkpoint_path),
            "anchor": anchor,
            "report": report,
        }

    def verify(self, *, session_id: Optional[str] = None, checkpoint_file: Optional[str] = None) -> Dict[str, Any]:
        with self._locked():
            return self._verify_locked(session_id=session_id, checkpoint_file=checkpoint_file)

    def _verify_locked(self, *, session_id: Optional[str] = None, checkpoint_file: Optional[str] = None) -> Dict[str, Any]:
        checkpoint_path = Path(checkpoint_file).expanduser() if checkpoint_file else self._select_checkpoint(session_id)
        failures: List[str] = []
        details: Dict[str, Any] = {"checkpointFile": str(checkpoint_path) if checkpoint_path else None}

        if not checkpoint_path or not checkpoint_path.exists():
            failures.append("checkpoint_not_found")
            return self._verification_result(False, failures, details, session_id=session_id)

        try:
            checkpoint = self._read_json(checkpoint_path)
            details["checkpoint"] = checkpoint
        except Exception as exc:
            failures.append(f"checkpoint_unreadable:{exc}")
            return self._verification_result(False, failures, details, session_id=session_id)

        expected_hash = checkpoint.get("checkpoint_hash")
        actual_hash = sha256_json({k: v for k, v in checkpoint.items() if k != "checkpoint_hash"})
        if actual_hash != expected_hash:
            failures.append("checkpoint_hash_mismatch")

        try:
            state = self.reduce_state(write=True)
            details["stateHash"] = state.get("state_hash")
            if state.get("state_hash") != checkpoint.get("state_hash"):
                failures.append("state_hash_mismatch")
            if state.get("event_count") != checkpoint.get("event_count"):
                failures.append("event_count_mismatch")
            if state.get("last_event_hash") != checkpoint.get("last_event_hash"):
                failures.append("last_event_hash_mismatch")
        except Exception as exc:
            failures.append(f"ledger_or_state_invalid:{exc}")

        anchor_path = self.home / "anchors" / f"{checkpoint.get('checkpoint_id')}.json"
        details["anchorFile"] = str(anchor_path)
        if not anchor_path.exists():
            failures.append("anchor_not_found")
        else:
            try:
                anchor = self._read_json(anchor_path)
                details["anchor"] = anchor
                if anchor.get("checkpoint_hash") != expected_hash:
                    failures.append("anchor_checkpoint_hash_mismatch")
                if not self._verify_anchor_signature(anchor):
                    failures.append("anchor_signature_mismatch")
            except Exception as exc:
                failures.append(f"anchor_unreadable:{exc}")

        if "ledger_or_state_invalid" not in ",".join(failures):
            try:
                details["indexRebuild"] = self._rebuild_index_locked(state=state)
            except Exception as exc:
                details.setdefault("warnings", []).append(f"index_rebuild_failed:{exc}")

        return self._verification_result(not failures, failures, details, session_id=session_id)

    def rehydrate(
        self,
        *,
        session_id: str = "default",
        query: str = "",
        max_results: int = 8,
    ) -> Dict[str, Any]:
        verification = self.verify(session_id=session_id)
        if not verification.get("ok"):
            return {
                "ok": False,
                "status": "FAIL_CLOSED",
                "error": "verification failed; refusing rehydrate",
                "verification": verification,
            }
        search_query = query or session_id or "recent continuity"
        search = self.search(search_query, max_results=max_results, session_id=session_id)
        lines = [
            "[Total Recall Rehydrate Authority]",
            f"status: PASS",
            f"session_id: {session_id}",
            f"checkpoint: {verification.get('checkpointFile')}",
            f"anchor: {verification.get('anchorFile')}",
            "",
            "Relevant recalled context:",
        ]
        for idx, result in enumerate(search.get("results", []), start=1):
            text = str(result.get("text") or "").replace("\n", " ")
            if len(text) > 500:
                text = text[:497] + "..."
            lines.append(f"{idx}. {text} [source: {result.get('source_ref')}]")
        if not search.get("results"):
            lines.append("- No matching prior memories found; checkpoint integrity still verified.")
        context_block = "\n".join(lines)
        payload = {
            "ok": True,
            "status": "PASS",
            "session_id": session_id,
            "query": search_query,
            "context_block": context_block,
            "verification": verification,
            "search": search,
        }
        payload["report"] = self._write_report("rehydrate", _safe_id(session_id), payload)
        return payload

    def search(
        self,
        query: str,
        *,
        max_results: int = 12,
        session_id: Optional[str] = None,
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        index_errors: List[str] = []
        try:
            with self._locked():
                state = self.reduce_state(write=True)
                status = self.index_status(state=state)
                stale = []
                for backend in ("lancedb", "qmd", "sqlite-fts"):
                    item = status.get("backends", {}).get(backend, {})
                    if item.get("available", item.get("ok")) and not item.get("fresh"):
                        stale.append(backend)
                if stale:
                    self._rebuild_index_locked(state=state, backends=tuple(stale))

            hybrid = self._search_derived_indexes(
                query,
                max_results=max_results,
                session_id=session_id,
                allowed_scopes=allowed_scopes,
            )
            if hybrid.get("results"):
                return hybrid
            index_errors = list(hybrid.get("errors") or [])
        except Exception as exc:
            index_errors.append(str(exc))

        lexical = self._search_lexical(
            query,
            max_results=max_results,
            session_id=session_id,
            allowed_scopes=allowed_scopes,
        )
        lexical["backend"] = "lexical-fallback"
        if index_errors:
            lexical["indexErrors"] = index_errors
        return lexical

    def rebuild_index(self, *, backends: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        with self._locked():
            state = self.reduce_state(write=True)
            return self._rebuild_index_locked(state=state, backends=tuple(backends) if backends else None)

    def index_status(self, *, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or self.reduce_state(write=False)
        sqlite_status = self._sqlite_index_status(state=state)
        lancedb_status = self._lancedb_index_status(state=state)
        qmd_status = self._qmd_index_status(state=state)
        return {
            "ok": sqlite_status.get("ok") is True,
            "backend": "derived-hybrid",
            "preferredOrder": ["lancedb", "qmd", "sqlite-fts", "lexical"],
            "fresh": bool(sqlite_status.get("fresh"))
            and (not lancedb_status.get("available") or bool(lancedb_status.get("fresh")))
            and (not qmd_status.get("available") or bool(qmd_status.get("fresh"))),
            "backends": {
                "sqlite-fts": sqlite_status,
                "lancedb": lancedb_status,
                "qmd": qmd_status,
            },
        }

    def _sqlite_index_status(self, *, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or self.reduce_state(write=False)
        status = {
            "ok": False,
            "available": True,
            "backend": "sqlite-fts",
            "schema": INDEX_SCHEMA_VERSION,
            "indexFile": str(self.index_file),
            "exists": self.index_file.exists(),
            "fresh": False,
            "eventCount": None,
            "lastEventHash": None,
            "stateHash": None,
            "documentCount": 0,
        }
        if not self.index_file.exists():
            status["error"] = "index_not_found"
            return status
        try:
            with sqlite3.connect(self.index_file) as conn:
                meta = dict(conn.execute("SELECT key, value FROM index_meta").fetchall())
                doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            status.update(
                {
                    "ok": True,
                    "schema": meta.get("schema", ""),
                    "builtAt": meta.get("built_at"),
                    "eventCount": int(meta.get("event_count") or 0),
                    "lastEventHash": meta.get("last_event_hash") or None,
                    "stateHash": meta.get("state_hash") or None,
                    "documentCount": int(doc_count or 0),
                }
            )
            status["fresh"] = (
                status["schema"] == INDEX_SCHEMA_VERSION
                and status["eventCount"] == state.get("event_count")
                and status["lastEventHash"] == state.get("last_event_hash")
                and status["stateHash"] == state.get("state_hash")
            )
        except Exception as exc:
            status["error"] = str(exc)
        return status

    def _lancedb_index_status(self, *, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or self.reduce_state(write=False)
        status = {
            "ok": False,
            "available": False,
            "backend": "lancedb",
            "schema": LANCEDB_INDEX_SCHEMA_VERSION,
            "indexDir": str(self.lancedb_dir),
            "metaFile": str(self.lancedb_meta_file),
            "exists": self.lancedb_meta_file.exists(),
            "fresh": False,
            "eventCount": None,
            "lastEventHash": None,
            "stateHash": None,
            "documentCount": 0,
        }
        if not self.config.enable_lancedb:
            status["error"] = "disabled"
            return status
        try:
            import lancedb  # noqa: F401  # type: ignore[import-not-found]

            status["available"] = True
        except Exception as exc:
            status["error"] = f"unavailable:{exc}"
            return status
        if not self.lancedb_meta_file.exists():
            status["error"] = "index_not_found"
            return status
        try:
            meta = self._read_json(self.lancedb_meta_file)
            status.update(
                {
                    "ok": meta.get("schema") == LANCEDB_INDEX_SCHEMA_VERSION,
                    "schema": meta.get("schema", ""),
                    "builtAt": meta.get("built_at"),
                    "eventCount": int(meta.get("event_count") or 0),
                    "lastEventHash": meta.get("last_event_hash") or None,
                    "stateHash": meta.get("state_hash") or None,
                    "documentCount": int(meta.get("document_count") or 0),
                    "embedding": meta.get("embedding"),
                    "dimensions": meta.get("dimensions"),
                }
            )
            status["fresh"] = (
                status["ok"]
                and status["eventCount"] == state.get("event_count")
                and status["lastEventHash"] == state.get("last_event_hash")
                and status["stateHash"] == state.get("state_hash")
            )
        except Exception as exc:
            status["error"] = str(exc)
        return status

    def _qmd_index_status(self, *, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = state or self.reduce_state(write=False)
        qmd = self._qmd_bin()
        status = {
            "ok": False,
            "available": bool(self.config.enable_qmd and qmd),
            "backend": "qmd",
            "schema": QMD_INDEX_SCHEMA_VERSION,
            "metaFile": str(self.qmd_meta_file),
            "docsDir": str(self.qmd_docs_dir),
            "exists": self.qmd_meta_file.exists(),
            "fresh": False,
            "eventCount": None,
            "lastEventHash": None,
            "stateHash": None,
            "documentCount": 0,
            "qmdBin": qmd,
            "qmdIndex": self._qmd_index_name(),
            "collection": self._qmd_collection_name(),
        }
        if not self.config.enable_qmd:
            status["error"] = "disabled"
            return status
        if not qmd:
            status["error"] = "qmd_not_found"
            return status
        if not self.qmd_meta_file.exists():
            status["error"] = "index_not_found"
            return status
        try:
            meta = self._read_json(self.qmd_meta_file)
            status.update(
                {
                    "ok": meta.get("schema") == QMD_INDEX_SCHEMA_VERSION,
                    "schema": meta.get("schema", ""),
                    "builtAt": meta.get("built_at"),
                    "eventCount": int(meta.get("event_count") or 0),
                    "lastEventHash": meta.get("last_event_hash") or None,
                    "stateHash": meta.get("state_hash") or None,
                    "documentCount": int(meta.get("document_count") or 0),
                    "docsDir": meta.get("docs_dir") or status["docsDir"],
                    "qmdIndex": meta.get("qmd_index") or status["qmdIndex"],
                    "collection": meta.get("collection") or status["collection"],
                    "embed": bool(meta.get("embed")),
                }
            )
            status["fresh"] = (
                status["ok"]
                and status["eventCount"] == state.get("event_count")
                and status["lastEventHash"] == state.get("last_event_hash")
                and status["stateHash"] == state.get("state_hash")
            )
        except Exception as exc:
            status["error"] = str(exc)
        return status

    def _search_lexical(
        self,
        query: str,
        *,
        max_results: int = 12,
        session_id: Optional[str] = None,
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        terms = [t.lower() for t in query.split() if t.strip()]
        scopes = set(allowed_scopes or self.config.allowed_scopes)
        candidates: List[Dict[str, Any]] = []

        for event in self._read_events(verify_chain=False):
            if event.get("scope") not in scopes:
                continue
            if session_id and event.get("session_id") != session_id:
                continue
            candidates.append({
                "kind": "event",
                "id": event.get("event_id"),
                "timestamp": event.get("timestamp"),
                "session_id": event.get("session_id"),
                "scope": event.get("scope"),
                "text": event.get("text", ""),
                "source_ref": f"ledger:{event.get('event_id')}",
                "metadata": event.get("metadata") or {},
            })

        for directory, kind in (
            (self.home / "reports", "report"),
            (self.home / "incidents", "incident"),
            (self.home / "checkpoints", "checkpoint"),
        ):
            for path in sorted(directory.glob("*.json")):
                try:
                    payload = self._read_json(path)
                except Exception:
                    continue
                text = canonical_json(payload)
                candidates.append({
                    "kind": kind,
                    "id": path.stem,
                    "timestamp": payload.get("created_at") or payload.get("timestamp"),
                    "session_id": payload.get("session_id"),
                    "scope": "internal",
                    "text": text,
                    "source_ref": str(path),
                    "metadata": {},
                })

        scored = []
        for item in candidates:
            haystack = str(item.get("text") or "").lower()
            score = sum(1 for term in terms if term in haystack) if terms else 1
            if query and query.lower() in haystack:
                score += 2
            if item.get("kind") == "event":
                score += 2
            if query and not score:
                continue
            scored.append({**item, "score": score})
        scored.sort(key=lambda x: (x["score"], x.get("timestamp") or ""), reverse=True)
        return {
            "ok": True,
            "query": query,
            "backend": "lexical",
            "results": scored[: max(1, max_results)],
            "count": len(scored),
        }

    def context_plan(self, query: str, *, session_id: str = "", max_results: int = 5) -> Dict[str, Any]:
        search = self.search(query, max_results=max_results, session_id=session_id or None)
        if not search.get("results"):
            return {"ok": True, "context": "", "search": search}
        lines = ["[Total Recall Context]"]
        for result in search["results"]:
            text = str(result.get("text") or "").replace("\n", " ")
            if len(text) > 360:
                text = text[:357] + "..."
            lines.append(f"- {text} [source: {result.get('source_ref')}]")
        return {"ok": True, "context": "\n".join(lines), "search": search}

    def create_incident(
        self,
        *,
        title: str,
        severity: str = "DEGRADED",
        status: str = "OPEN",
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        incident_id = self._new_id("incident")
        incident = {
            "schema": "total-recall-incident-v1",
            "incident_id": incident_id,
            "title": title,
            "severity": severity,
            "status": status,
            "summary": summary,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "timeline": [{"at": utc_now(), "event": "created", "summary": summary}],
            "metadata": metadata or {},
        }
        path = self.home / "incidents" / f"{incident_id}.json"
        self._write_json(path, incident)
        self._write_markdown(path.with_suffix(".md"), self._incident_markdown(incident))
        return {"ok": True, "incident": incident, "incidentFile": str(path)}

    def list_incidents(self, *, status: str = "") -> Dict[str, Any]:
        incidents = []
        for path in sorted((self.home / "incidents").glob("*.json")):
            try:
                item = self._read_json(path)
            except Exception:
                continue
            if status and item.get("status") != status:
                continue
            item["incidentFile"] = str(path)
            incidents.append(item)
        incidents.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
        return {"ok": True, "incidents": incidents, "count": len(incidents)}

    def update_incident(self, incident_id: str, *, note: str = "", status: str = "") -> Dict[str, Any]:
        path = self.home / "incidents" / f"{_safe_id(incident_id)}.json"
        if not path.exists():
            return {"ok": False, "error": "incident not found", "incident_id": incident_id}
        incident = self._read_json(path)
        if status:
            incident["status"] = status
        if note:
            incident.setdefault("timeline", []).append({"at": utc_now(), "event": "note", "summary": note})
        incident["updated_at"] = utc_now()
        self._write_json(path, incident)
        self._write_markdown(path.with_suffix(".md"), self._incident_markdown(incident))
        return {"ok": True, "incident": incident, "incidentFile": str(path)}

    def external_ingest(
        self,
        *,
        text: str,
        source: str,
        source_kind: str = "manual",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        item_id = self._new_id("external")
        item = {
            "schema": "total-recall-external-v1",
            "external_id": item_id,
            "created_at": utc_now(),
            "source": source,
            "source_kind": source_kind,
            "text": text,
            "status": "quarantine",
            "metadata": metadata or {},
        }
        path = self.home / "external-memory" / "quarantine" / f"{item_id}.json"
        self._write_json(path, item)
        return {"ok": True, "external": item, "externalFile": str(path)}

    def external_list(self, *, queue: str = "quarantine") -> Dict[str, Any]:
        qdir = self.home / "external-memory" / _safe_id(queue)
        items = []
        for path in sorted(qdir.glob("*.json")):
            try:
                payload = self._read_json(path)
            except Exception:
                continue
            payload["externalFile"] = str(path)
            items.append(payload)
        return {"ok": True, "queue": queue, "items": items, "count": len(items)}

    def external_promote(self, external_id: str, *, session_id: str = "default") -> Dict[str, Any]:
        source = self._find_external(external_id)
        if not source:
            return {"ok": False, "error": "external item not found", "external_id": external_id}
        item = self._read_json(source)
        item["status"] = "promoted"
        item["promoted_at"] = utc_now()
        dest = self.home / "external-memory" / "promoted" / source.name
        self._write_json(dest, item)
        source.unlink(missing_ok=True)
        event = self.ingest(
            kind="external_promoted",
            text=item.get("text", ""),
            session_id=session_id,
            source=f"external:{item.get('source')}",
            metadata={"external_id": item.get("external_id"), **(item.get("metadata") or {})},
        )
        return {"ok": True, "external": item, "externalFile": str(dest), "event": event.get("event")}

    def external_reject(self, external_id: str, *, reason: str = "") -> Dict[str, Any]:
        source = self._find_external(external_id)
        if not source:
            return {"ok": False, "error": "external item not found", "external_id": external_id}
        item = self._read_json(source)
        item["status"] = "rejected"
        item["rejected_at"] = utc_now()
        item["reject_reason"] = reason
        dest = self.home / "external-memory" / "rejected" / source.name
        self._write_json(dest, item)
        source.unlink(missing_ok=True)
        return {"ok": True, "external": item, "externalFile": str(dest)}

    def export_bundle(self, out: str, *, include_index: bool = False) -> Dict[str, Any]:
        out_path = Path(out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        include_dirs = [
            "ledger",
            "state",
            "checkpoints",
            "anchors",
            "reports",
            "incidents",
            "external-memory",
            "keys",
        ]
        if include_index:
            include_dirs.append("index")

        files: List[Path] = []
        for rel_dir in include_dirs:
            directory = self.home / rel_dir
            if directory.exists():
                files.extend(path for path in directory.rglob("*") if path.is_file())

        manifest = {
            "schema": "total-recall-export-v1",
            "version": VERSION,
            "created_at": utc_now(),
            "include_index": include_index,
            "files": [],
        }
        for path in sorted(files):
            rel = path.relative_to(self.home).as_posix()
            manifest["files"].append({
                "path": rel,
                "sha256": self._file_sha256(path),
                "bytes": path.stat().st_size,
            })

        with tempfile.TemporaryDirectory(prefix="total-recall-export.") as tmpdir:
            manifest_path = Path(tmpdir) / "MANIFEST.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with tarfile.open(out_path, "w:gz") as tar:
                tar.add(manifest_path, arcname="MANIFEST.json")
                for path in sorted(files):
                    tar.add(path, arcname=path.relative_to(self.home).as_posix())

        return {
            "ok": True,
            "bundle": str(out_path),
            "fileCount": len(files),
            "includeIndex": include_index,
            "manifestHash": hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest(),
        }

    def import_bundle(self, bundle: str, *, replace: bool = False) -> Dict[str, Any]:
        bundle_path = Path(bundle).expanduser()
        if not bundle_path.exists():
            return {"ok": False, "error": "bundle_not_found", "bundle": str(bundle_path)}
        if self.ledger_file.exists() and self.ledger_file.stat().st_size > 0 and not replace:
            return {"ok": False, "error": "target_not_empty", "home": str(self.home), "hint": "pass replace=True to overwrite"}

        with tempfile.TemporaryDirectory(prefix="total-recall-import.") as tmpdir:
            tmp_home = Path(tmpdir) / "store"
            tmp_home.mkdir(parents=True, exist_ok=True)
            tmp_root = tmp_home.resolve()
            with tarfile.open(bundle_path, "r:gz") as tar:
                members = tar.getmembers()
                for member in members:
                    if member.issym() or member.islnk():
                        return {"ok": False, "error": "unsafe_bundle_link", "path": member.name}
                    rel = Path(member.name)
                    target = (tmp_home / rel).resolve()
                    try:
                        target.relative_to(tmp_root)
                    except ValueError:
                        return {"ok": False, "error": "unsafe_bundle_path", "path": member.name}
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = tar.extractfile(member)
                        if source is None:
                            return {"ok": False, "error": "unsafe_bundle_file", "path": member.name}
                        with target.open("wb") as dest:
                            shutil.copyfileobj(source, dest)
                    else:
                        return {"ok": False, "error": "unsafe_bundle_member", "path": member.name}

            manifest_path = tmp_home / "MANIFEST.json"
            if not manifest_path.exists():
                return {"ok": False, "error": "manifest_not_found"}
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in manifest.get("files", []):
                rel = Path(str(item.get("path") or ""))
                if rel.is_absolute() or ".." in rel.parts:
                    return {"ok": False, "error": "unsafe_manifest_path", "path": str(rel)}
                path = tmp_home / rel
                if not path.exists():
                    return {"ok": False, "error": "manifest_file_missing", "path": str(rel)}
                if self._file_sha256(path) != item.get("sha256"):
                    return {"ok": False, "error": "manifest_hash_mismatch", "path": str(rel)}

            if replace:
                for rel_dir in ("ledger", "state", "checkpoints", "anchors", "reports", "incidents", "external-memory", "keys", "index"):
                    shutil.rmtree(self.home / rel_dir, ignore_errors=True)
            self._ensure_layout()
            for item in manifest.get("files", []):
                rel = Path(str(item.get("path") or ""))
                src = tmp_home / rel
                dest = self.home / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

        verification = self.verify()
        return {
            "ok": bool(verification.get("ok")),
            "bundle": str(bundle_path),
            "home": str(self.home),
            "fileCount": len(manifest.get("files", [])),
            "verification": verification,
        }

    def doctor(self) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []

        def add(name: str, ok: bool, **details: Any) -> None:
            checks.append({"name": name, "ok": ok, **details})

        required_dirs = ["ledger", "state", "checkpoints", "anchors", "reports", "incidents", "external-memory", "index", "keys"]
        for rel_dir in required_dirs:
            add(f"dir:{rel_dir}", (self.home / rel_dir).is_dir(), path=str(self.home / rel_dir))

        try:
            state = self.reduce_state(write=True)
            add("ledger_hash_chain", True, eventCount=state.get("event_count"), lastEventHash=state.get("last_event_hash"))
        except Exception as exc:
            add("ledger_hash_chain", False, error=str(exc))
            state = None

        latest_checkpoint = self._latest_file(self.home / "checkpoints", "*.json")
        add("checkpoint_present", latest_checkpoint is not None, checkpointFile=str(latest_checkpoint) if latest_checkpoint else None)
        if latest_checkpoint:
            verification = self.verify(checkpoint_file=str(latest_checkpoint))
            add("verify_latest_checkpoint", bool(verification.get("ok")), status=verification.get("status"), failures=verification.get("failures", []))
        else:
            verification = {"ok": False, "status": "NO_CHECKPOINT"}

        try:
            index_status = self.index_status(state=state)
            add("derived_index_status", bool(index_status.get("fresh")), fresh=index_status.get("fresh"), backends=index_status.get("backends"))
        except Exception as exc:
            add("derived_index_status", False, error=str(exc))

        try:
            public_key = self._ed25519_public_key_hex()
            add("ed25519_public_key", bool(public_key), keyId=self._key_id())
        except Exception as exc:
            add("ed25519_public_key", False, error=str(exc))

        ok = all(check.get("ok") for check in checks if check["name"] != "checkpoint_present") and latest_checkpoint is not None
        payload = {"ok": ok, "status": "PASS" if ok else "DEGRADED", "home": str(self.home), "checks": checks}
        payload["report"] = self._write_report("doctor", "latest", payload)
        return payload

    def backup_status(self, out_dir: str) -> Dict[str, Any]:
        directory = Path(out_dir).expanduser()
        backups = self._list_backup_files(directory)
        return {
            "ok": True,
            "backupDir": str(directory),
            "count": len(backups),
            "totalBytes": sum(path.stat().st_size for path in backups),
            "latest": str(backups[0]) if backups else None,
            "backups": [
                {
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "modified": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                    .replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                }
                for path in backups
            ],
        }

    def backup_run(self, out_dir: str, *, keep: int = 14, include_index: bool = False) -> Dict[str, Any]:
        directory = Path(out_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        bundle = directory / f"total-recall-backup-{stamp}-{secrets.token_hex(3)}.tar.gz"

        exported = self.export_bundle(str(bundle), include_index=include_index)
        doctor = self.doctor()
        verification = self.verify()
        pruned: List[str] = []
        if keep > 0:
            backups = self._list_backup_files(directory)
            for path in backups[max(0, keep) :]:
                path.unlink(missing_ok=True)
                pruned.append(str(path))

        ok = bool(exported.get("ok")) and bool(doctor.get("ok")) and bool(verification.get("ok"))
        payload = {
            "ok": ok,
            "status": "PASS" if ok else "FAIL_CLOSED",
            "backup": exported,
            "doctor": doctor,
            "verification": verification,
            "retention": {"keep": keep, "pruned": pruned},
            "backupStatus": self.backup_status(str(directory)),
        }
        payload["report"] = self._write_report("backup", "latest", payload)
        return payload

    def rehydrate_status(self, *, session_key: Optional[str] = None, agent: Optional[str] = None) -> Dict[str, Any]:
        session_id = session_key or (f"agent:{agent}:main" if agent else "default")
        latest = self._latest_file(self.home / "reports", f"rehydrate_{_safe_id(session_id)}*.json")
        if not latest:
            latest = self._latest_file(self.home / "reports", "rehydrate_*.json")
        if not latest:
            return {"ok": False, "error": "rehydrate report not found", "home": str(self.home)}
        payload = self._read_json(latest)
        return {"ok": bool(payload.get("ok")), "reportFile": str(latest), **payload}

    # Backward-compatible alias for the old facade.
    def grep(self, query: str, **kwargs: Any) -> Dict[str, Any]:
        return self.search(query, **kwargs)

    def _verification_result(
        self,
        ok: bool,
        failures: List[str],
        details: Dict[str, Any],
        *,
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        status = "PASS" if ok else "FAIL_CLOSED"
        payload = {
            "ok": ok,
            "status": status,
            "session_id": session_id,
            "failures": failures,
            **details,
            "checked_at": utc_now(),
        }
        payload["report"] = self._write_report("verify", _safe_id(session_id or "latest"), payload)
        if not ok:
            incident = self.create_incident(
                title="Continuity verification failed",
                severity="FAIL_CLOSED",
                summary=", ".join(failures),
                metadata={
                    "session_id": session_id,
                    "failures": list(failures),
                    "checkpointFile": details.get("checkpointFile"),
                    "anchorFile": details.get("anchorFile"),
                    "checked_at": payload.get("checked_at"),
                },
            )
            payload["incident"] = incident.get("incident")
        return payload

    def _rebuild_index_locked(
        self,
        *,
        state: Optional[Dict[str, Any]] = None,
        backends: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        state = state or self.reduce_state(write=True)
        events = self._read_events(verify_chain=True)
        requested = set(backends or ("sqlite-fts", "lancedb", "qmd"))
        results: Dict[str, Any] = {}
        if "sqlite" in requested:
            requested.add("sqlite-fts")
        if "sqlite-fts" in requested:
            results["sqlite-fts"] = self._rebuild_sqlite_index_locked(state=state, events=events)
        if "lancedb" in requested:
            results["lancedb"] = self._rebuild_lancedb_index_locked(state=state, events=events)
        if "qmd" in requested:
            results["qmd"] = self._rebuild_qmd_index_locked(state=state, events=events)
        return {"ok": bool(results.get("sqlite-fts", {"ok": True}).get("ok", False)), "index": self.index_status(state=state), "rebuilt": results}

    def _rebuild_sqlite_index_locked(self, *, state: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
        tmp_path = self.index_file.with_name(f".{self.index_file.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        tmp_path.unlink(missing_ok=True)
        self.index_file.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(tmp_path) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                CREATE TABLE index_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    timestamp TEXT,
                    session_id TEXT,
                    scope TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE documents_fts
                USING fts5(text, content='documents', content_rowid='id')
                """
            )
            conn.execute("CREATE INDEX documents_scope_idx ON documents(scope)")
            conn.execute("CREATE INDEX documents_session_idx ON documents(session_id)")
            conn.execute("CREATE INDEX documents_timestamp_idx ON documents(timestamp)")

            for event in events:
                cursor = conn.execute(
                    """
                    INSERT INTO documents
                      (kind, item_id, timestamp, session_id, scope, source_ref, text, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "event",
                        str(event.get("event_id") or ""),
                        event.get("timestamp"),
                        event.get("session_id") or "default",
                        event.get("scope") or "private",
                        f"ledger:{event.get('event_id')}",
                        str(event.get("text") or ""),
                        canonical_json(event.get("metadata") or {}),
                    ),
                )
                conn.execute(
                    "INSERT INTO documents_fts(rowid, text) VALUES (?, ?)",
                    (cursor.lastrowid, str(event.get("text") or "")),
                )

            meta = {
                "schema": INDEX_SCHEMA_VERSION,
                "backend": "sqlite-fts",
                "built_at": utc_now(),
                "event_count": str(state.get("event_count", 0)),
                "last_event_hash": str(state.get("last_event_hash") or ""),
                "state_hash": str(state.get("state_hash") or ""),
                "source": "ledger",
                "authority": "ledger/checkpoints/anchors",
            }
            conn.executemany("INSERT INTO index_meta(key, value) VALUES (?, ?)", sorted(meta.items()))
            conn.commit()

        shutil.move(str(tmp_path), str(self.index_file))
        return self._sqlite_index_status(state=state)

    def _rebuild_lancedb_index_locked(self, *, state: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.config.enable_lancedb:
            return {"ok": False, "available": False, "backend": "lancedb", "error": "disabled"}
        try:
            import lancedb  # type: ignore[import-not-found]
        except Exception as exc:
            return {"ok": False, "available": False, "backend": "lancedb", "error": f"unavailable:{exc}"}

        shutil.rmtree(self.lancedb_dir, ignore_errors=True)
        self.lancedb_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for event in events:
            text = str(event.get("text") or "")
            rows.append(
                {
                    "vector": self._embed_text(text),
                    "kind": "event",
                    "item_id": str(event.get("event_id") or ""),
                    "timestamp": str(event.get("timestamp") or ""),
                    "session_id": str(event.get("session_id") or "default"),
                    "scope": str(event.get("scope") or "private"),
                    "source_ref": f"ledger:{event.get('event_id')}",
                    "text": text,
                    "metadata_json": canonical_json(event.get("metadata") or {}),
                }
            )
        try:
            db = lancedb.connect(str(self.lancedb_dir))
            if rows:
                db.create_table("documents", data=rows)
            meta = self._index_meta_payload(
                schema=LANCEDB_INDEX_SCHEMA_VERSION,
                backend="lancedb",
                state=state,
                extra={"embedding": "total-recall-hash-bow-v1", "dimensions": EMBEDDING_DIMENSIONS},
            )
            self._write_json(self.lancedb_meta_file, meta)
            return self._lancedb_index_status(state=state)
        except Exception as exc:
            return {"ok": False, "available": True, "backend": "lancedb", "error": str(exc)}

    def _rebuild_qmd_index_locked(self, *, state: Dict[str, Any], events: List[Dict[str, Any]]) -> Dict[str, Any]:
        qmd = self._qmd_bin()
        if not self.config.enable_qmd:
            return {"ok": False, "available": False, "backend": "qmd", "error": "disabled"}
        if not qmd:
            return {"ok": False, "available": False, "backend": "qmd", "error": "qmd_not_found"}

        shutil.rmtree(self.qmd_docs_dir, ignore_errors=True)
        events_dir = self.qmd_docs_dir / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        for event in events:
            event_id = _safe_id(str(event.get("event_id") or "event"))
            text = str(event.get("text") or "")
            content = [
                f"# {event_id}",
                "",
                f"- source_ref: ledger:{event.get('event_id')}",
                f"- session_id: {event.get('session_id') or 'default'}",
                f"- scope: {event.get('scope') or 'private'}",
                f"- timestamp: {event.get('timestamp') or ''}",
                "",
                text,
                "",
            ]
            self._write_markdown(events_dir / f"{event_id}.md", "\n".join(content))

        index_name = self._qmd_index_name()
        collection = self._qmd_collection_name()
        self._run_qmd([qmd, "--index", index_name, "collection", "remove", collection], timeout=30, check=False)
        added = self._run_qmd(
            [qmd, "--index", index_name, "collection", "add", str(self.qmd_docs_dir), "--name", collection, "--mask", "**/*.md"],
            timeout=120,
            check=False,
        )
        if not added.get("ok"):
            return {"ok": False, "available": True, "backend": "qmd", "error": added.get("stderr") or added.get("stdout") or "collection_add_failed"}
        if self.config.qmd_embed:
            self._run_qmd([qmd, "--index", index_name, "embed"], timeout=600, check=False)
        meta = self._index_meta_payload(
            schema=QMD_INDEX_SCHEMA_VERSION,
            backend="qmd",
            state=state,
            extra={
                "qmd_bin": qmd,
                "qmd_index": index_name,
                "collection": collection,
                "docs_dir": str(self.qmd_docs_dir),
                "embed": self.config.qmd_embed,
            },
        )
        self._write_json(self.qmd_meta_file, meta)
        return self._qmd_index_status(state=state)

    def _search_index(
        self,
        query: str,
        *,
        max_results: int,
        session_id: Optional[str],
        allowed_scopes: Optional[Iterable[str]],
    ) -> Dict[str, Any]:
        if not self.index_file.exists():
            return {"ok": False, "error": "index_not_found"}
        scopes = sorted(set(allowed_scopes or self.config.allowed_scopes))
        if not scopes:
            return {"ok": True, "query": query, "backend": "sqlite-fts", "results": [], "count": 0}

        scope_clause = ",".join("?" for _ in scopes)
        params: List[Any] = list(scopes)
        session_clause = ""
        if session_id:
            session_clause = " AND d.session_id = ?"
            params.append(session_id)

        limit = max(1, int(max_results))
        tokens = self._fts_tokens(query)
        results: List[Dict[str, Any]] = []
        with sqlite3.connect(self.index_file) as conn:
            conn.row_factory = sqlite3.Row
            if tokens:
                match_expr = " ".join(f'"{token}"' for token in tokens)
                rows = conn.execute(
                    f"""
                    SELECT
                      d.kind,
                      d.item_id,
                      d.timestamp,
                      d.session_id,
                      d.scope,
                      d.source_ref,
                      d.text,
                      d.metadata_json,
                      -bm25(documents_fts) AS score
                    FROM documents_fts
                    JOIN documents d ON d.id = documents_fts.rowid
                    WHERE documents_fts MATCH ?
                      AND d.scope IN ({scope_clause})
                      {session_clause}
                    ORDER BY score DESC, d.timestamp DESC
                    LIMIT ?
                    """,
                    [match_expr, *params, limit],
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT
                      d.kind,
                      d.item_id,
                      d.timestamp,
                      d.session_id,
                      d.scope,
                      d.source_ref,
                      d.text,
                      d.metadata_json,
                      1.0 AS score
                    FROM documents d
                    WHERE d.scope IN ({scope_clause})
                      {session_clause}
                    ORDER BY d.timestamp DESC
                    LIMIT ?
                    """,
                    [*params, limit],
                ).fetchall()

        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except Exception:
                metadata = {}
            results.append(
                {
                    "kind": row["kind"],
                    "id": row["item_id"],
                    "timestamp": row["timestamp"],
                    "session_id": row["session_id"],
                    "scope": row["scope"],
                    "text": row["text"],
                    "source_ref": row["source_ref"],
                    "metadata": metadata,
                    "score": row["score"],
                }
            )
        return {
            "ok": True,
            "query": query,
            "backend": "sqlite-fts",
            "indexFile": str(self.index_file),
            "results": results,
            "count": len(results),
        }

    def _search_derived_indexes(
        self,
        query: str,
        *,
        max_results: int,
        session_id: Optional[str],
        allowed_scopes: Optional[Iterable[str]],
    ) -> Dict[str, Any]:
        errors: List[str] = []
        merged: Dict[str, Dict[str, Any]] = {}
        backends_used: List[str] = []

        for backend, searcher in (
            ("lancedb", self._search_lancedb_index),
            ("qmd", self._search_qmd_index),
            ("sqlite-fts", self._search_index),
        ):
            try:
                payload = searcher(
                    query,
                    max_results=max_results,
                    session_id=session_id,
                    allowed_scopes=allowed_scopes,
                )
            except Exception as exc:
                errors.append(f"{backend}:{exc}")
                continue
            if not payload.get("ok"):
                if payload.get("error"):
                    errors.append(f"{backend}:{payload.get('error')}")
                continue
            results = payload.get("results") or []
            if results:
                backends_used.append(backend)
            for result in results:
                source_ref = str(result.get("source_ref") or result.get("id") or "")
                if not source_ref:
                    continue
                score = float(result.get("score") or 0)
                existing = merged.get(source_ref)
                if existing is None:
                    item = dict(result)
                    item["backend_sources"] = [backend]
                    item["score"] = score
                    merged[source_ref] = item
                else:
                    existing.setdefault("backend_sources", []).append(backend)
                    existing["score"] = max(float(existing.get("score") or 0), score)

        results = list(merged.values())
        results.sort(key=lambda x: (float(x.get("score") or 0), x.get("timestamp") or ""), reverse=True)
        limit = max(1, int(max_results))
        return {
            "ok": True,
            "query": query,
            "backend": "derived-hybrid" if backends_used else "derived-none",
            "backends": backends_used,
            "results": results[:limit],
            "count": len(results),
            "errors": errors,
        }

    def _search_lancedb_index(
        self,
        query: str,
        *,
        max_results: int,
        session_id: Optional[str],
        allowed_scopes: Optional[Iterable[str]],
    ) -> Dict[str, Any]:
        if not self.config.enable_lancedb:
            return {"ok": False, "backend": "lancedb", "error": "disabled"}
        try:
            import lancedb  # type: ignore[import-not-found]
        except Exception as exc:
            return {"ok": False, "backend": "lancedb", "error": f"unavailable:{exc}"}
        if not self.lancedb_meta_file.exists():
            return {"ok": False, "backend": "lancedb", "error": "index_not_found"}

        scopes = set(allowed_scopes or self.config.allowed_scopes)
        limit = max(1, int(max_results))
        try:
            db = lancedb.connect(str(self.lancedb_dir))
            table = db.open_table("documents")
            rows = table.search(self._embed_text(query)).limit(limit * 4).to_list()
        except Exception as exc:
            return {"ok": False, "backend": "lancedb", "error": str(exc)}

        results = []
        for row in rows:
            if row.get("scope") not in scopes:
                continue
            if session_id and row.get("session_id") != session_id:
                continue
            try:
                metadata = json.loads(row.get("metadata_json") or "{}")
            except Exception:
                metadata = {}
            distance = float(row.get("_distance") or 0)
            results.append(
                {
                    "kind": row.get("kind"),
                    "id": row.get("item_id"),
                    "timestamp": row.get("timestamp"),
                    "session_id": row.get("session_id"),
                    "scope": row.get("scope"),
                    "text": row.get("text") or "",
                    "source_ref": row.get("source_ref"),
                    "metadata": metadata,
                    "score": 1.0 / (1.0 + distance),
                    "distance": distance,
                }
            )
            if len(results) >= limit:
                break
        return {
            "ok": True,
            "query": query,
            "backend": "lancedb",
            "indexDir": str(self.lancedb_dir),
            "results": results,
            "count": len(results),
        }

    def _search_qmd_index(
        self,
        query: str,
        *,
        max_results: int,
        session_id: Optional[str],
        allowed_scopes: Optional[Iterable[str]],
    ) -> Dict[str, Any]:
        qmd = self._qmd_bin()
        if not self.config.enable_qmd:
            return {"ok": False, "backend": "qmd", "error": "disabled"}
        if not qmd:
            return {"ok": False, "backend": "qmd", "error": "qmd_not_found"}
        if not self.qmd_meta_file.exists():
            return {"ok": False, "backend": "qmd", "error": "index_not_found"}

        limit = max(1, int(max_results))
        meta = self._read_json(self.qmd_meta_file)
        index_name = str(meta.get("qmd_index") or self._qmd_index_name())
        collection = str(meta.get("collection") or self._qmd_collection_name())
        cmd = [qmd, "--index", index_name, "search", query, "--json", "-n", str(limit * 4), "--collection", collection]
        payload = self._run_qmd(cmd, timeout=45, check=False)
        if not payload.get("ok"):
            return {"ok": False, "backend": "qmd", "error": payload.get("stderr") or payload.get("stdout") or "search_failed"}
        try:
            stdout = (payload.get("stdout") or "").strip()
            if not stdout or stdout.startswith("No results found"):
                rows = []
            else:
                rows = json.loads(stdout)
        except Exception as exc:
            return {"ok": False, "backend": "qmd", "error": f"invalid_json:{exc}"}

        events_by_id = {str(e.get("event_id")): e for e in self._read_events(verify_chain=False)}
        scopes = set(allowed_scopes or self.config.allowed_scopes)
        results = []
        for row in rows if isinstance(rows, list) else []:
            file_ref = str(row.get("file") or "")
            event_id = str(row.get("title") or "").strip() or (Path(file_ref.split("/")[-1]).stem if file_ref else "")
            event = events_by_id.get(event_id)
            if not event:
                continue
            if event.get("scope") not in scopes:
                continue
            if session_id and event.get("session_id") != session_id:
                continue
            results.append(
                {
                    "kind": "event",
                    "id": event.get("event_id"),
                    "timestamp": event.get("timestamp"),
                    "session_id": event.get("session_id"),
                    "scope": event.get("scope"),
                    "text": event.get("text", ""),
                    "source_ref": f"ledger:{event.get('event_id')}",
                    "metadata": event.get("metadata") or {},
                    "score": float(row.get("score") or 0),
                    "qmd_file": file_ref,
                    "snippet": row.get("snippet"),
                }
            )
            if len(results) >= limit:
                break
        return {
            "ok": True,
            "query": query,
            "backend": "qmd",
            "qmdIndex": index_name,
            "collection": collection,
            "results": results,
            "count": len(results),
        }

    def _fts_tokens(self, query: str) -> List[str]:
        return [token.lower() for token in re.findall(r"[\w.-]+", query or "") if token.strip()]

    def _embed_text(self, text: str) -> List[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        tokens = self._fts_tokens(text)
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign
        norm = sum(v * v for v in vector) ** 0.5
        if norm:
            vector = [v / norm for v in vector]
        return vector

    def _index_meta_payload(
        self,
        *,
        schema: str,
        backend: str,
        state: Dict[str, Any],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "schema": schema,
            "backend": backend,
            "built_at": utc_now(),
            "event_count": state.get("event_count", 0),
            "last_event_hash": state.get("last_event_hash"),
            "state_hash": state.get("state_hash"),
            "document_count": state.get("event_count", 0),
            "source": "ledger",
            "authority": "ledger/checkpoints/anchors",
        }
        payload.update(extra or {})
        return payload

    def _qmd_bin(self) -> str:
        if not self.config.enable_qmd:
            return ""
        if self.config.qmd_bin:
            return self.config.qmd_bin if Path(self.config.qmd_bin).exists() else ""
        return shutil.which("qmd") or ""

    def _qmd_index_name(self) -> str:
        return f"total-recall-{hashlib.sha256(str(self.home).encode('utf-8')).hexdigest()[:12]}"

    def _qmd_collection_name(self) -> str:
        return "total-recall"

    def _run_qmd(self, cmd: List[str], *, timeout: int, check: bool) -> Dict[str, Any]:
        try:
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"ok": False, "cmd": cmd, "error": f"timeout:{timeout}s", "stdout": "", "stderr": ""}
        except Exception as exc:
            return {"ok": False, "cmd": cmd, "error": str(exc), "stdout": "", "stderr": ""}
        ok = cp.returncode == 0
        if check and not ok:
            return {"ok": False, "cmd": cmd, "returncode": cp.returncode, "stdout": cp.stdout, "stderr": cp.stderr}
        return {"ok": ok, "cmd": cmd, "returncode": cp.returncode, "stdout": cp.stdout, "stderr": cp.stderr}

    def _ensure_layout(self) -> None:
        for rel in (
            "ledger",
            "state",
            "checkpoints",
            "anchors",
            "reports",
            "incidents",
            "external-memory/inbox",
            "external-memory/quarantine",
            "external-memory/promoted",
            "external-memory/rejected",
            "index",
            "keys",
        ):
            (self.home / rel).mkdir(parents=True, exist_ok=True)
        self.ledger_file.touch(exist_ok=True)
        self.lock_file.touch(exist_ok=True)

    @contextmanager
    def _locked(self, *, shared: bool = False):
        import fcntl

        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_file.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _read_events(self, *, verify_chain: bool) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        prev: Optional[str] = None
        if not self.ledger_file.exists():
            return events
        for line_no, line in enumerate(self.ledger_file.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            event = json.loads(line)
            event_hash = event.get("hash")
            base = {k: v for k, v in event.items() if k != "hash"}
            if verify_chain:
                if event_hash != sha256_json(base):
                    raise ValueError(f"ledger hash mismatch at line {line_no}")
                if event.get("prev_hash") != prev:
                    raise ValueError(f"ledger prev_hash mismatch at line {line_no}")
            prev = event_hash
            events.append(event)
        return events

    def _last_event_hash(self) -> Optional[str]:
        events = self._read_events(verify_chain=False)
        return events[-1].get("hash") if events else None

    def _state_summary(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_count": state.get("event_count", 0),
            "session_count": len(state.get("sessions") or {}),
            "memory_count": len(state.get("memories") or []),
            "promoted_external_count": len(state.get("promoted_external") or []),
        }

    def _write_anchor(self, checkpoint_path: Path, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        checkpoint_hash = checkpoint["checkpoint_hash"]
        anchor = {
            "schema": "total-recall-anchor-v1",
            "anchor_id": checkpoint["checkpoint_id"],
            "checkpoint_id": checkpoint["checkpoint_id"],
            "checkpoint_file": str(checkpoint_path),
            "checkpoint_hash": checkpoint_hash,
            "algorithm": SIGNING_ALGORITHM,
            "key_id": self._key_id(),
            "public_key": self._ed25519_public_key_hex(),
            "signature": self._signature(checkpoint_hash),
            "created_at": utc_now(),
        }
        anchor_path = self.home / "anchors" / f"{checkpoint['checkpoint_id']}.json"
        self._write_json(anchor_path, anchor)
        anchor["anchorFile"] = str(anchor_path)
        return anchor

    def _secret_key(self) -> bytes:
        if not self.key_file.exists():
            self.key_file.write_text(secrets.token_hex(32), encoding="utf-8")
            try:
                self.key_file.chmod(0o600)
            except Exception:
                pass
        return self.key_file.read_text(encoding="utf-8").strip().encode("utf-8")

    def _key_id(self) -> str:
        return hashlib.sha256(bytes.fromhex(self._ed25519_public_key_hex())).hexdigest()[:16]

    def _signature(self, checkpoint_hash: str) -> str:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(self._ed25519_private_key_hex()))
        signature = private_key.sign(checkpoint_hash.encode("utf-8"))
        return signature.hex()

    def _verify_anchor_signature(self, anchor: Dict[str, Any]) -> bool:
        algorithm = anchor.get("algorithm") or LEGACY_SIGNING_ALGORITHM
        checkpoint_hash = str(anchor.get("checkpoint_hash") or "")
        signature = str(anchor.get("signature") or "")
        if algorithm == SIGNING_ALGORITHM:
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

                public_key_hex = str(anchor.get("public_key") or self._ed25519_public_key_hex())
                public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
                public_key.verify(bytes.fromhex(signature), checkpoint_hash.encode("utf-8"))
                return True
            except Exception:
                return False
        if algorithm == LEGACY_SIGNING_ALGORITHM:
            expected_sig = hmac.new(self._secret_key(), checkpoint_hash.encode("utf-8"), hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected_sig)
        return False

    def _ed25519_private_key_hex(self) -> str:
        if not self.ed25519_private_key_file.exists():
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            private_key = Ed25519PrivateKey.generate()
            private_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            self.ed25519_private_key_file.parent.mkdir(parents=True, exist_ok=True)
            self.ed25519_private_key_file.write_text(private_bytes.hex() + "\n", encoding="utf-8")
            self.ed25519_public_key_file.write_text(public_bytes.hex() + "\n", encoding="utf-8")
            try:
                self.ed25519_private_key_file.chmod(0o600)
                self.ed25519_public_key_file.chmod(0o644)
            except Exception:
                pass
        return self.ed25519_private_key_file.read_text(encoding="utf-8").strip()

    def _ed25519_public_key_hex(self) -> str:
        if not self.ed25519_public_key_file.exists():
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(self._ed25519_private_key_hex()))
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            self.ed25519_public_key_file.write_text(public_bytes.hex() + "\n", encoding="utf-8")
        return self.ed25519_public_key_file.read_text(encoding="utf-8").strip()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _select_checkpoint(self, session_id: Optional[str]) -> Optional[Path]:
        if session_id:
            latest = self._latest_file(self.home / "checkpoints", f"checkpoint_{_safe_id(session_id)}_*.json")
            if latest:
                return latest
        return self._latest_file(self.home / "checkpoints", "*.json")

    def _latest_file(self, directory: Path, pattern: str) -> Optional[Path]:
        files = [p for p in directory.glob(pattern) if p.is_file()]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    def _list_backup_files(self, directory: Path) -> List[Path]:
        if not directory.exists():
            return []
        return sorted(
            [path for path in directory.glob("total-recall-backup-*.tar.gz") if path.is_file()],
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )

    def _write_report(self, kind: str, subject: str, payload: Dict[str, Any]) -> Dict[str, str]:
        report_id = f"{kind}_{_safe_id(subject)}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        json_path = self.home / "reports" / f"{report_id}.json"
        md_path = self.home / "reports" / f"{report_id}.md"
        self._write_json(json_path, payload)
        md = [
            f"# Total Recall {kind.title()} Report",
            "",
            f"- Status: `{payload.get('status') or ('PASS' if payload.get('ok') else 'FAIL')}`",
            f"- Created: `{utc_now()}`",
            f"- Subject: `{subject}`",
            "",
            "```json",
            json.dumps(payload, indent=2, ensure_ascii=False),
            "```",
            "",
        ]
        self._write_markdown(md_path, "\n".join(md))
        return {"json": str(json_path), "markdown": str(md_path)}

    def _find_external(self, external_id: str) -> Optional[Path]:
        safe = _safe_id(external_id)
        for queue in ("inbox", "quarantine", "promoted", "rejected"):
            path = self.home / "external-memory" / queue / f"{safe}.json"
            if path.exists():
                return path
        return None

    def _new_id(self, prefix: str) -> str:
        return f"{_safe_id(prefix)}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"

    def _read_json(self, path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        shutil.move(str(tmp), str(path))

    def _write_markdown(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _incident_markdown(self, incident: Dict[str, Any]) -> str:
        lines = [
            f"# {incident.get('title')}",
            "",
            f"- Incident: `{incident.get('incident_id')}`",
            f"- Severity: `{incident.get('severity')}`",
            f"- Status: `{incident.get('status')}`",
            f"- Created: `{incident.get('created_at')}`",
            "",
            incident.get("summary") or "",
            "",
            "## Timeline",
        ]
        for item in incident.get("timeline") or []:
            lines.append(f"- `{item.get('at')}` {item.get('event')}: {item.get('summary')}")
        return "\n".join(lines) + "\n"
