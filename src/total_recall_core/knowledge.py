from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone, timedelta
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .api import canonical_json, sha256_json, utc_now, _safe_id


KNOWLEDGE_SCHEMA_VERSION = "total-recall-knowledge-sqlite-v2"
SYNTHESIS_SCHEMA_VERSION = "total-recall-knowledge-synthesis-v1"
EVAL_SCHEMA_VERSION = "total-recall-knowledge-eval-v1"
COMPILED_TRUTH_SCHEMA_VERSION = "total-recall-compiled-truth-v1"

_STOPWORDS = {
    "about",
    "after",
    "again",
    "agent",
    "also",
    "and",
    "because",
    "before",
    "being",
    "between",
    "could",
    "from",
    "have",
    "into",
    "memory",
    "should",
    "that",
    "the",
    "their",
    "there",
    "this",
    "total",
    "recall",
    "when",
    "where",
    "with",
    "would",
}

_ENTITY_TYPES = {
    "decision": "decision",
    "decided": "decision",
    "must": "decision",
    "promise": "decision",
    "promises": "decision",
    "supersedes": "decision",
    "todo": "task",
    "next": "task",
    "implement": "task",
    "fix": "task",
    "blocker": "task",
}

_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "developer message",
    "system prompt",
    "tool call",
    "do not reveal",
    "exfiltrate",
)

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(api[\s_-]?key\s*[:=]?\s*)[^\s,;]+"),
    re.compile(r"(?i)\b(token\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
)


class KnowledgeEngine:
    """Derived, rebuildable knowledge layer over Total Recall authority."""

    def __init__(self, core: Any) -> None:
        self.core = core
        self.home = core.home

    @property
    def knowledge_dir(self) -> Path:
        return self.home / "knowledge"

    @property
    def index_dir(self) -> Path:
        return self.knowledge_dir / "index"

    @property
    def index_file(self) -> Path:
        return self.index_dir / "knowledge.sqlite"

    @property
    def graph_dir(self) -> Path:
        return self.knowledge_dir / "graph"

    @property
    def synthesis_dir(self) -> Path:
        return self.knowledge_dir / "synthesis"

    @property
    def compiled_dir(self) -> Path:
        return self.knowledge_dir / "compiled"

    @property
    def eval_dir(self) -> Path:
        return self.knowledge_dir / "eval"

    def ensure_layout(self) -> None:
        for rel in (
            "index",
            "graph",
            "synthesis/staging",
            "synthesis/runs",
            "synthesis/promoted",
            "compiled",
            "quarantine",
            "reports",
            "eval",
            "providers",
        ):
            (self.knowledge_dir / rel).mkdir(parents=True, exist_ok=True)

    def status(self) -> Dict[str, Any]:
        self.ensure_layout()
        try:
            state = self.core.reduce_state(write=True)
            state_ok = True
            state_error = ""
        except Exception as exc:
            state = {}
            state_ok = False
            state_error = str(exc)
        index_status = self.index_status(state=state if state_ok else None)
        graph_status = self.graph_status()
        synthesis_status = self.synthesis_status()
        compiled_status = self.compiled_truth_status()
        passed = state_ok and index_status.get("ok") is True and graph_status.get("ok") is True
        return {
            "ok": state_ok,
            "status": "PASS" if passed else "DEGRADED",
            "schema": KNOWLEDGE_SCHEMA_VERSION,
            "home": str(self.knowledge_dir),
            "authority": "ledger/checkpoints/anchors",
            "derived": True,
            "state": {
                "ok": state_ok,
                "error": state_error or None,
                "eventCount": state.get("event_count"),
                "lastEventHash": state.get("last_event_hash"),
                "stateHash": state.get("state_hash"),
            },
            "index": index_status,
            "graph": graph_status,
            "synthesis": synthesis_status,
            "compiledTruth": compiled_status,
        }

    def index_status(self, *, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.ensure_layout()
        if state is None:
            try:
                state = self.core.reduce_state(write=False)
            except Exception as exc:
                return {
                    "ok": False,
                    "schema": KNOWLEDGE_SCHEMA_VERSION,
                    "indexFile": str(self.index_file),
                    "exists": self.index_file.exists(),
                    "fresh": False,
                    "error": f"state_unreadable:{exc}",
                }
        status = {
            "ok": False,
            "schema": KNOWLEDGE_SCHEMA_VERSION,
            "indexFile": str(self.index_file),
            "exists": self.index_file.exists(),
            "fresh": False,
            "sourceCount": 0,
            "entityCount": 0,
            "edgeCount": 0,
            "quarantineCount": 0,
            "eventCount": None,
            "lastEventHash": None,
            "stateHash": None,
        }
        if not self.index_file.exists():
            status["error"] = "knowledge_index_not_found"
            return status
        try:
            with sqlite3.connect(self.index_file) as conn:
                meta = dict(conn.execute("SELECT key, value FROM knowledge_meta").fetchall())
                status.update(
                    {
                        "ok": meta.get("schema") == KNOWLEDGE_SCHEMA_VERSION,
                        "schema": meta.get("schema", ""),
                        "builtAt": meta.get("built_at"),
                        "eventCount": int(meta.get("event_count") or 0),
                        "lastEventHash": meta.get("last_event_hash") or None,
                        "stateHash": meta.get("state_hash") or None,
                        "sourceCount": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
                        "entityCount": conn.execute("SELECT COUNT(*) FROM entities WHERE status = 'active'").fetchone()[0],
                        "edgeCount": conn.execute("SELECT COUNT(*) FROM edges WHERE status = 'active'").fetchone()[0],
                        "quarantineCount": conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0],
                    }
                )
                uncited_entities = conn.execute("SELECT COUNT(*) FROM entities WHERE status = 'active' AND (source_ref = '' OR evidence_hash = '')").fetchone()[0]
                uncited_edges = conn.execute("SELECT COUNT(*) FROM edges WHERE status = 'active' AND (source_ref = '' OR evidence_hash = '')").fetchone()[0]
                status["uncitedActiveItems"] = int(uncited_entities or 0) + int(uncited_edges or 0)
            status["fresh"] = (
                status["ok"]
                and status["eventCount"] == state.get("event_count")
                and status["lastEventHash"] == state.get("last_event_hash")
                and status["stateHash"] == state.get("state_hash")
                and status.get("uncitedActiveItems") == 0
            )
        except Exception as exc:
            status["error"] = str(exc)
        return status

    def rebuild_index(self) -> Dict[str, Any]:
        self.ensure_layout()
        with self.core._locked():
            state = self.core.reduce_state(write=True)
            events = self.core._read_events(verify_chain=True)
            tmp_path = self.index_file.with_name(f".{self.index_file.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
            tmp_path.unlink(missing_ok=True)
            self.index_file.parent.mkdir(parents=True, exist_ok=True)
            source_count = 0
            redaction_count = 0
            injection_count = 0
            entity_count = 0
            edge_count = 0
            with sqlite3.connect(tmp_path) as conn:
                self._create_schema(conn)
                for event in events:
                    source_count += 1
                    sanitized, redactions, flags = self._sanitize(str(event.get("text") or ""))
                    redaction_count += redactions
                    injection_count += 1 if flags else 0
                    source_ref = f"ledger:{event.get('event_id')}"
                    text_hash = sha256_json({"text": sanitized})
                    evidence_hash = self._evidence_hash(source_ref=source_ref, text_hash=text_hash, event_hash=str(event.get("hash") or ""))
                    metadata = event.get("metadata") or {}
                    effective_timestamp = self._source_effective_timestamp(event)
                    cursor = conn.execute(
                        """
                        INSERT INTO sources
                          (event_id, kind, timestamp, effective_timestamp, session_id, scope, source_ref, text, sanitized_text, text_hash, evidence_hash, redaction_count, injection_flags_json, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(event.get("event_id") or ""),
                            str(event.get("kind") or "event"),
                            event.get("timestamp"),
                            effective_timestamp,
                            str(event.get("session_id") or "default"),
                            str(event.get("scope") or "private"),
                            source_ref,
                            str(event.get("text") or ""),
                            sanitized,
                            text_hash,
                            evidence_hash,
                            redactions,
                            canonical_json(flags),
                            canonical_json(metadata),
                        ),
                    )
                    conn.execute("INSERT INTO sources_fts(rowid, sanitized_text) VALUES (?, ?)", (cursor.lastrowid, sanitized))
                    entities, edges, quarantined = self._extract_graph(event, sanitized, source_ref, evidence_hash)
                    for item in quarantined:
                        conn.execute(
                            "INSERT INTO quarantine(kind, source_ref, evidence_hash, reason, payload_json) VALUES (?, ?, ?, ?, ?)",
                            (item["kind"], source_ref, evidence_hash, item["reason"], canonical_json(item["payload"])),
                        )
                    for entity in entities:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO entities
                              (entity_id, name, normalized_name, type, source_ref, evidence_hash, confidence, status, scope, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                            """,
                            (
                                entity["entity_id"],
                                entity["name"],
                                entity["normalized_name"],
                                entity["type"],
                                source_ref,
                                evidence_hash,
                                entity["confidence"],
                                str(event.get("scope") or "private"),
                                event.get("timestamp") or utc_now(),
                            ),
                        )
                        entity_count += 1
                    for edge in edges:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO edges
                              (edge_id, source_entity_id, target_entity_id, relation, source_ref, evidence_hash, confidence, status, scope, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                            """,
                            (
                                edge["edge_id"],
                                edge["source_entity_id"],
                                edge["target_entity_id"],
                                edge["relation"],
                                source_ref,
                                evidence_hash,
                                edge["confidence"],
                                str(event.get("scope") or "private"),
                                event.get("timestamp") or utc_now(),
                            ),
                        )
                        edge_count += 1
                meta = {
                    "schema": KNOWLEDGE_SCHEMA_VERSION,
                    "built_at": utc_now(),
                    "event_count": str(state.get("event_count") or 0),
                    "last_event_hash": str(state.get("last_event_hash") or ""),
                    "state_hash": str(state.get("state_hash") or ""),
                    "source": "ledger-only",
                    "authority": "ledger/checkpoints/anchors",
                    "redaction_count": str(redaction_count),
                    "injection_source_count": str(injection_count),
                }
                conn.executemany("INSERT INTO knowledge_meta(key, value) VALUES (?, ?)", sorted(meta.items()))
                conn.commit()
            shutil.move(str(tmp_path), str(self.index_file))
        status = self.index_status(state=state)
        self._write_graph_snapshot()
        compiled = self.compiled_truth_build(index_status=status)
        return {
            "ok": bool(status.get("ok")) and status.get("uncitedActiveItems", 0) == 0,
            "status": "PASS" if status.get("uncitedActiveItems", 0) == 0 else "FAIL_CLOSED",
            "index": status,
            "compiledTruth": compiled,
            "rebuilt": {
                "sources": source_count,
                "entities": entity_count,
                "edges": edge_count,
                "redactions": redaction_count,
                "injectionSources": injection_count,
            },
        }

    def graph_status(self) -> Dict[str, Any]:
        if not self.index_file.exists():
            return {"ok": False, "status": "MISSING", "error": "knowledge_index_not_found", "entityCount": 0, "edgeCount": 0, "uncitedActiveItems": 0}
        try:
            with sqlite3.connect(self.index_file) as conn:
                entity_count = conn.execute("SELECT COUNT(*) FROM entities WHERE status = 'active'").fetchone()[0]
                edge_count = conn.execute("SELECT COUNT(*) FROM edges WHERE status = 'active'").fetchone()[0]
                quarantine_count = conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
                uncited_entities = conn.execute("SELECT COUNT(*) FROM entities WHERE status = 'active' AND (source_ref = '' OR evidence_hash = '')").fetchone()[0]
                uncited_edges = conn.execute("SELECT COUNT(*) FROM edges WHERE status = 'active' AND (source_ref = '' OR evidence_hash = '')").fetchone()[0]
            uncited = int(uncited_entities or 0) + int(uncited_edges or 0)
            return {
                "ok": uncited == 0,
                "status": "PASS" if uncited == 0 else "FAIL_CLOSED",
                "entityCount": int(entity_count or 0),
                "edgeCount": int(edge_count or 0),
                "quarantineCount": int(quarantine_count or 0),
                "uncitedActiveItems": uncited,
                "snapshot": str(self.graph_dir / "latest.json"),
            }
        except Exception as exc:
            return {"ok": False, "status": "FAIL_CLOSED", "error": str(exc), "entityCount": 0, "edgeCount": 0, "uncitedActiveItems": 0}

    def rebuild_graph(self) -> Dict[str, Any]:
        rebuilt = self.rebuild_index()
        return {"ok": bool(rebuilt.get("ok")), "status": rebuilt.get("status"), "graph": self.graph_status(), "index": rebuilt.get("index")}

    def graph_inspect(
        self,
        *,
        entity: str = "",
        source_ref: str = "",
        limit: int = 20,
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        if not self.index_status().get("fresh"):
            self.rebuild_index()
        scopes = list(allowed_scopes or self.core.config.allowed_scopes)
        limit = max(1, min(int(limit or 20), 100))
        entity = str(entity or "").strip()
        source_ref = str(source_ref or "").strip()
        if not self.index_file.exists():
            return {"ok": False, "status": "MISSING", "error": "knowledge_index_not_found", "entities": [], "edges": [], "sources": []}
        with sqlite3.connect(self.index_file) as conn:
            conn.row_factory = sqlite3.Row
            scope_clause = ",".join("?" for _ in scopes)
            entity_where = ["status = 'active'", f"scope IN ({scope_clause})"]
            entity_params: List[Any] = list(scopes)
            if entity:
                entity_where.append("normalized_name LIKE ?")
                entity_params.append(f"%{self._normalize_entity(entity)}%")
            if source_ref:
                entity_where.append("source_ref = ?")
                entity_params.append(source_ref)
            entities = [
                dict(row)
                for row in conn.execute(
                    f"SELECT entity_id, name, normalized_name, type, source_ref, evidence_hash, confidence, scope, created_at FROM entities WHERE {' AND '.join(entity_where)} ORDER BY confidence DESC, created_at DESC LIMIT ?",
                    [*entity_params, limit],
                ).fetchall()
            ]
            refs = list(dict.fromkeys([str(item.get("source_ref")) for item in entities if item.get("source_ref")] + ([source_ref] if source_ref else [])))
            edge_params: List[Any] = [*scopes]
            edge_where = ["status = 'active'", f"scope IN ({scope_clause})"]
            if entities:
                ids = [str(item.get("entity_id")) for item in entities if item.get("entity_id")]
                placeholders = ",".join("?" for _ in ids)
                edge_where.append(f"(source_entity_id IN ({placeholders}) OR target_entity_id IN ({placeholders}))")
                edge_params.extend([*ids, *ids])
            elif source_ref:
                edge_where.append("source_ref = ?")
                edge_params.append(source_ref)
            edges = [
                dict(row)
                for row in conn.execute(
                    f"SELECT edge_id, source_entity_id, target_entity_id, relation, source_ref, evidence_hash, confidence, scope, created_at FROM edges WHERE {' AND '.join(edge_where)} ORDER BY confidence DESC, created_at DESC LIMIT ?",
                    [*edge_params, limit * 2],
                ).fetchall()
            ]
            refs.extend(str(edge.get("source_ref")) for edge in edges if edge.get("source_ref"))
            sources = self._sources_for_refs(conn, list(dict.fromkeys(refs))[:limit])
        return {
            "ok": True,
            "status": "PASS",
            "query": {"entity": entity or None, "source_ref": source_ref or None, "limit": limit},
            "scopeFilter": {"allowedScopes": scopes, "filtered": True},
            "entities": entities,
            "edges": edges,
            "sources": sources,
            "citations": [self._citation(source) for source in sources],
        }

    def graph_traverse(
        self,
        entity: str,
        *,
        depth: int = 2,
        limit: int = 40,
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        if not self.index_status().get("fresh"):
            self.rebuild_index()
        scopes = list(allowed_scopes or self.core.config.allowed_scopes)
        depth = max(1, min(int(depth or 2), 4))
        limit = max(1, min(int(limit or 40), 200))
        needle = self._normalize_entity(entity)
        if not needle:
            return {"ok": False, "status": "ERROR", "error": "entity is required"}
        with sqlite3.connect(self.index_file) as conn:
            conn.row_factory = sqlite3.Row
            scope_clause = ",".join("?" for _ in scopes)
            start = [
                dict(row)
                for row in conn.execute(
                    f"SELECT entity_id, name, normalized_name, type, source_ref, evidence_hash, confidence, scope, created_at FROM entities WHERE status = 'active' AND scope IN ({scope_clause}) AND normalized_name LIKE ? ORDER BY confidence DESC LIMIT ?",
                    [*scopes, f"%{needle}%", min(limit, 20)],
                ).fetchall()
            ]
            frontier = {str(item["entity_id"]) for item in start}
            seen_entities: Dict[str, Dict[str, Any]] = {str(item["entity_id"]): item for item in start}
            seen_edges: Dict[str, Dict[str, Any]] = {}
            for step in range(depth):
                if not frontier or len(seen_edges) >= limit:
                    break
                ids = sorted(frontier)
                placeholders = ",".join("?" for _ in ids)
                rows = [
                    dict(row)
                    for row in conn.execute(
                        f"SELECT edge_id, source_entity_id, target_entity_id, relation, source_ref, evidence_hash, confidence, scope, created_at FROM edges WHERE status = 'active' AND scope IN ({scope_clause}) AND (source_entity_id IN ({placeholders}) OR target_entity_id IN ({placeholders})) ORDER BY confidence DESC, created_at DESC LIMIT ?",
                        [*scopes, *ids, *ids, max(1, limit - len(seen_edges))],
                    ).fetchall()
                ]
                next_frontier: set[str] = set()
                for edge in rows:
                    edge_id = str(edge.get("edge_id"))
                    seen_edges[edge_id] = edge
                    for endpoint in (str(edge.get("source_entity_id") or ""), str(edge.get("target_entity_id") or "")):
                        if endpoint and endpoint not in seen_entities:
                            next_frontier.add(endpoint)
                if not next_frontier:
                    frontier = set()
                    continue
                entity_ids = sorted(next_frontier)[:limit]
                entity_placeholders = ",".join("?" for _ in entity_ids)
                entity_rows = [
                    dict(row)
                    for row in conn.execute(
                        f"SELECT entity_id, name, normalized_name, type, source_ref, evidence_hash, confidence, scope, created_at FROM entities WHERE status = 'active' AND scope IN ({scope_clause}) AND entity_id IN ({entity_placeholders})",
                        [*scopes, *entity_ids],
                    ).fetchall()
                ]
                for item in entity_rows:
                    seen_entities[str(item["entity_id"])] = item
                frontier = {str(item["entity_id"]) for item in entity_rows}
            refs = list(dict.fromkeys([str(item.get("source_ref")) for item in seen_entities.values() if item.get("source_ref")] + [str(edge.get("source_ref")) for edge in seen_edges.values() if edge.get("source_ref")]))
            sources = self._sources_for_refs(conn, refs[:limit])
        return {
            "ok": True,
            "status": "PASS",
            "query": {"entity": entity, "depth": depth, "limit": limit},
            "scopeFilter": {"allowedScopes": scopes, "filtered": True},
            "start": start,
            "entities": list(seen_entities.values())[:limit],
            "edges": list(seen_edges.values())[:limit],
            "sources": sources,
            "citations": [self._citation(source) for source in sources],
        }

    def graph_timeline(
        self,
        entity: str,
        *,
        at_time: str = "",
        limit: int = 40,
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        if not self.index_status().get("fresh"):
            self.rebuild_index()
        scopes = list(allowed_scopes or self.core.config.allowed_scopes)
        limit = max(1, min(int(limit or 40), 200))
        needle = self._normalize_entity(entity)
        if not needle:
            return {"ok": False, "status": "ERROR", "error": "entity is required"}
        as_of_dt = self._parse_time(at_time) if at_time else None
        if not self.index_file.exists():
            return {"ok": False, "status": "MISSING", "error": "knowledge_index_not_found", "timeline": []}
        with sqlite3.connect(self.index_file) as conn:
            conn.row_factory = sqlite3.Row
            scope_clause = ",".join("?" for _ in scopes)
            rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT DISTINCT s.*
                    FROM sources s
                    LEFT JOIN entities e ON e.source_ref = s.source_ref
                    WHERE s.scope IN ({scope_clause})
                      AND (lower(s.sanitized_text) LIKE ? OR e.normalized_name LIKE ?)
                    ORDER BY COALESCE(s.effective_timestamp, s.timestamp) ASC
                    LIMIT ?
                    """,
                    [*scopes, f"%{needle}%", f"%{needle}%", limit],
                ).fetchall()
            ]
        sources = [self._public_source(row) for row in rows]
        current = []
        future = []
        for source in sources:
            item_time = self._parse_time(str(source.get("effective_timestamp") or source.get("timestamp") or ""))
            record = {
                "timestamp": source.get("effective_timestamp") or source.get("timestamp"),
                "ledger_timestamp": source.get("timestamp"),
                "source_ref": source.get("source_ref"),
                "evidence_hash": source.get("evidence_hash"),
                "kind": source.get("kind"),
                "scope": source.get("scope"),
                "text": str(source.get("text") or "")[:700],
                "freshness": self._freshness_record_for_source(source, as_of=as_of_dt),
            }
            if as_of_dt and item_time and item_time > as_of_dt:
                future.append(record)
            else:
                current.append(record)
        return {
            "ok": True,
            "status": "PASS",
            "query": {"entity": entity, "at_time": at_time or None, "limit": limit},
            "scopeFilter": {"allowedScopes": scopes, "filtered": True},
            "asOf": current,
            "afterAsOf": future,
            "timeline": current + future,
            "citations": [self._citation(source) for source in sources],
        }

    def freshness_report(
        self,
        *,
        entity: str = "",
        category: str = "",
        at_time: str = "",
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        if not self.index_status().get("fresh"):
            self.rebuild_index()
        scopes = list(allowed_scopes or self.core.config.allowed_scopes)
        as_of = self._parse_time(at_time) if at_time else datetime.now(timezone.utc)
        items = self._freshness_items(scopes=scopes, as_of=as_of, entity=entity, category=category)
        counts: Dict[str, int] = {}
        for item in items:
            counts[item["freshness"]] = counts.get(item["freshness"], 0) + 1
        return {
            "ok": True,
            "status": "PASS",
            "schema": "total-recall-freshness-report-v1",
            "asOf": as_of.isoformat().replace("+00:00", "Z"),
            "query": {"entity": entity or None, "category": category or None},
            "scopeFilter": {"allowedScopes": scopes, "filtered": True},
            "counts": counts,
            "items": items,
            "citations": [item["citation"] for item in items if item.get("citation")],
        }

    def compiled_truth_status(self) -> Dict[str, Any]:
        self.ensure_layout()
        markdown = self.compiled_dir / "truth.md"
        payload_file = self.compiled_dir / "truth.json"
        if not markdown.exists() or not payload_file.exists():
            return {"ok": True, "status": "NO_PROJECTION", "markdown": str(markdown), "json": str(payload_file)}
        try:
            payload = json.loads(payload_file.read_text(encoding="utf-8"))
            index = self.index_status()
            fresh = bool(index.get("fresh")) and payload.get("indexStateHash") == index.get("stateHash")
            return {
                "ok": True,
                "status": "PASS" if fresh else "STALE",
                "schema": payload.get("schema"),
                "projectionHash": payload.get("projectionHash"),
                "created_at": payload.get("created_at"),
                "fresh": fresh,
                "markdown": str(markdown),
                "json": str(payload_file),
            }
        except Exception as exc:
            return {"ok": False, "status": "FAIL_CLOSED", "error": str(exc), "markdown": str(markdown), "json": str(payload_file)}

    def compiled_truth_build(self, *, index_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.ensure_layout()
        index = index_status or self.index_status()
        if not index.get("fresh"):
            rebuilt = self.rebuild_index()
            index = rebuilt.get("index") or self.index_status()
        sources = self._top_sources(limit=80)
        entities = self._entity_rows(limit=80)
        decisions = [source for source in sources if self._looks_decision(str(source.get("text") or ""))][:20]
        tasks = [source for source in sources if self._looks_task(str(source.get("text") or ""))][:20]
        promises = [source for source in sources if "promise" in str(source.get("text") or "").lower()][:20]
        timeline = sources[:30]
        sections = {
            "decisions": self._compiled_items(decisions),
            "promises": self._compiled_items(promises),
            "tasks": self._compiled_items(tasks),
            "timeline": self._compiled_items(timeline),
            "entities": entities[:40],
        }
        payload = {
            "ok": True,
            "status": "PASS",
            "schema": COMPILED_TRUTH_SCHEMA_VERSION,
            "created_at": utc_now(),
            "authority": "derived-projection-ledger-remains-authority",
            "indexStateHash": index.get("stateHash"),
            "indexLastEventHash": index.get("lastEventHash"),
            "sourceCount": len(sources),
            "sections": sections,
        }
        payload["projectionHash"] = sha256_json({"schema": payload["schema"], "indexStateHash": payload.get("indexStateHash"), "sections": sections})
        markdown = self._compiled_truth_markdown(payload)
        truth_json = self.compiled_dir / "truth.json"
        truth_md = self.compiled_dir / "truth.md"
        self._write_json(truth_json, payload)
        truth_md.write_text(markdown, encoding="utf-8")
        return {"ok": True, "status": "PASS", "schema": COMPILED_TRUTH_SCHEMA_VERSION, "projectionHash": payload["projectionHash"], "markdown": str(truth_md), "json": str(truth_json), "sourceCount": len(sources)}

    def compiled_truth_show(self, *, format_: str = "json") -> Dict[str, Any]:
        status = self.compiled_truth_status()
        if status.get("status") in {"NO_PROJECTION", "STALE"}:
            status = self.compiled_truth_build()
        if not status.get("ok"):
            return status
        format_ = format_ if format_ in {"json", "md", "text"} else "json"
        if format_ == "json":
            payload = json.loads((self.compiled_dir / "truth.json").read_text(encoding="utf-8"))
            return {"ok": True, "status": "PASS", "format": "json", "projection": payload, "markdown": str(self.compiled_dir / "truth.md"), "json": str(self.compiled_dir / "truth.json")}
        markdown = (self.compiled_dir / "truth.md").read_text(encoding="utf-8")
        if format_ == "text":
            markdown = re.sub(r"`([^`]+)`", r"\1", markdown)
            markdown = re.sub(r"#+\s*", "", markdown)
        return {"ok": True, "status": "PASS", "format": format_, "text": markdown, "markdown": str(self.compiled_dir / "truth.md"), "json": str(self.compiled_dir / "truth.json")}

    def query(
        self,
        query: str,
        *,
        mode: str = "normal",
        session_id: str = "",
        max_results: int = 8,
        at_time: str = "",
        allowed_scopes: Optional[Iterable[str]] = None,
        federate: Optional[Iterable[str]] = None,
        federation_authorized: bool = False,
        external_providers: Optional[Iterable[str]] = None,
        external_provider_authorized: bool = False,
        allow_rebuild: bool = True,
        audit: bool = True,
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        query = str(query or "").strip()
        if not query:
            return {"ok": False, "status": "ERROR", "error": "query is required"}
        mode = mode if mode in {"fast", "normal", "strict", "explore"} else "normal"
        status = self.index_status()
        if not status.get("fresh"):
            if not allow_rebuild:
                return {
                    "ok": False,
                    "status": "DEGRADED",
                    "error": "knowledge_index_not_fresh",
                    "index": status,
                    "citations": [],
                    "evidence": [],
                    "warnings": ["read_only_query_requires_fresh_index"],
                }
            rebuilt = self.rebuild_index()
            status = rebuilt.get("index") or self.index_status()
        scopes = list(allowed_scopes or self.core.config.allowed_scopes)
        candidates = self._query_candidates(query, mode=mode, session_id=session_id, max_results=max_results, at_time=at_time, allowed_scopes=scopes)
        confidence = self._confidence(candidates, mode=mode)
        refused = mode == "strict" and confidence["level"] in {"none", "low"}
        answer = self._answer(query, candidates, confidence=confidence, refused=refused, at_time=at_time)
        citations = [self._citation(item) for item in candidates[: max(1, max_results)] if item.get("source_ref")]
        graph = self._graph_context([item.get("source_ref") for item in candidates], scopes=scopes)
        freshness = self._freshness_for_sources(candidates, scopes=scopes, at_time=at_time)
        warnings = []
        if at_time:
            warnings.append("temporal_filter_applied")
        if any(self._looks_conflicting(str(item.get("text") or "")) for item in candidates):
            warnings.append("possible_conflict_or_supersession_present")
        if any(item.get("freshness") in {"stale", "superseded"} for item in freshness.get("items", [])):
            warnings.append("freshness_attention_required")
        provider_calls = [
            {
                "provider": "local-hash-rerank",
                "local": True,
                "scopesSent": scopes,
                "redactionCount": sum(int(item.get("redaction_count") or 0) for item in candidates),
                "authorization": "local-derived-store",
                "status": "PASS",
                "latencyMs": round((time.perf_counter() - started) * 1000, 3),
            }
        ]
        external_calls, external_warnings = self._external_provider_calls(
            external_providers or [],
            scopes=scopes,
            authorized=external_provider_authorized,
            redaction_count=sum(int(item.get("redaction_count") or 0) for item in candidates),
        )
        provider_calls.extend(external_calls)
        warnings.extend(external_warnings)
        requested_federation = [str(item).strip() for item in federate or [] if str(item).strip()]
        federation = self._federated_query(
            requested_federation,
            query=query,
            mode=mode,
            session_id=session_id,
            max_results=max_results,
            at_time=at_time,
            scopes=scopes,
            authorized=federation_authorized,
        )
        if requested_federation and not federation_authorized:
            warnings.append("federation_requires_explicit_authorization")
        payload = {
            "ok": True,
            "status": "REFUSED" if refused else "PASS",
            "mode": mode,
            "query": query,
            "answer": answer,
            "confidence": confidence,
            "citations": citations,
            "evidence": candidates[: max(1, max_results)],
            "graph": graph,
            "freshness": freshness,
            "scopeFilter": {"allowedScopes": scopes, "filtered": True},
            "temporal": {"asOf": at_time or None, "applied": bool(at_time)},
            "providerCalls": provider_calls,
            "federation": federation,
            "warnings": warnings,
        }
        payload["providerReport"] = None
        if audit:
            payload["providerReport"] = self._write_provider_report(
                payload["providerCalls"],
                query=query,
                mode=mode,
                session_id=session_id,
                scopes=scopes,
                at_time=at_time,
                federation=federation,
            )
        return payload

    def synthesis_status(self) -> Dict[str, Any]:
        self.ensure_layout()
        latest = self.synthesis_dir / "latest.json"
        if not latest.exists():
            return {"ok": True, "status": "NO_SYNTHESIS", "latest": None}
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
            return {"ok": bool(payload.get("ok", True)), "status": payload.get("status") or "PASS", "latest": str(latest), "run": payload}
        except Exception as exc:
            return {"ok": False, "status": "FAIL_CLOSED", "latest": str(latest), "error": str(exc)}

    def synthesize_run(self) -> Dict[str, Any]:
        self.ensure_layout()
        if not self.index_status().get("fresh"):
            self.rebuild_index()
        run_id = f"synthesis_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
        staging = self.synthesis_dir / "staging" / run_id
        final = self.synthesis_dir / "runs" / run_id
        staging.mkdir(parents=True, exist_ok=True)
        sources = self._top_sources(limit=40)
        graph = self.graph_status()
        proposals = self._synthesis_proposals(run_id, sources)
        payload = {
            "ok": True,
            "status": "PASS",
            "schema": SYNTHESIS_SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": utc_now(),
            "authority": "derived-provisional-owner-promotion-required",
            "sourceCount": len(sources),
            "graph": graph,
            "artifacts": {},
            "proposals": proposals,
        }
        artifacts = {
            "daily_brief.md": self._daily_brief_markdown(sources, proposals),
            "entity_summaries.json": canonical_json({"entities": self._entity_rows(limit=50)}),
            "decision_timeline.json": canonical_json({"decisions": [s for s in sources if self._looks_decision(str(s.get("text") or ""))]}),
            "contradiction_report.json": canonical_json({"possibleConflicts": [s for s in sources if self._looks_conflicting(str(s.get("text") or ""))]}),
            "open_questions.json": canonical_json({"questions": [p for p in proposals if p["type"] == "open_question"]}),
        }
        for name, content in artifacts.items():
            path = staging / name
            path.write_text(content + ("\n" if not content.endswith("\n") else ""), encoding="utf-8")
            payload["artifacts"][name] = {"path": str(final / name), "sha256": self._file_sha256(path)}
        self._validate_synthesis(payload, sources)
        self._write_json(staging / "run.json", payload)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            shutil.rmtree(final)
        shutil.move(str(staging), str(final))
        payload["runDir"] = str(final)
        self._write_json(self.synthesis_dir / "latest.json", payload)
        return payload

    def synthesize_promote(self, proposal_id: str, *, session_id: str = "default") -> Dict[str, Any]:
        latest = self.synthesis_dir / "latest.json"
        if not latest.exists():
            return {"ok": False, "status": "ERROR", "error": "synthesis_not_found"}
        payload = json.loads(latest.read_text(encoding="utf-8"))
        proposal = next((p for p in payload.get("proposals", []) if p.get("proposal_id") == proposal_id), None)
        if not proposal:
            return {"ok": False, "status": "ERROR", "error": "proposal_not_found", "proposal_id": proposal_id}
        event = self.core.ingest(
            kind="knowledge_synthesis_promoted",
            text=str(proposal.get("text") or ""),
            session_id=session_id,
            source=f"knowledge:synthesis:{payload.get('run_id')}",
            metadata={"proposal_id": proposal_id, "citations": proposal.get("citations") or [], "owner_authorized": True},
        )
        promoted_path = self.synthesis_dir / "promoted" / f"{_safe_id(proposal_id)}.json"
        self._write_json(promoted_path, {"proposal": proposal, "event": event.get("event"), "promoted_at": utc_now()})
        return {"ok": True, "status": "PASS", "proposal": proposal, "event": event.get("event"), "promotedFile": str(promoted_path)}

    def evaluate_run(self) -> Dict[str, Any]:
        self.ensure_layout()
        index = self.index_status()
        if not index.get("fresh"):
            rebuilt = self.rebuild_index()
            index = rebuilt.get("index") or self.index_status()
        graph = self.graph_status()
        report_exclusion = self._reports_excluded()
        synthesis = self.synthesis_status()
        query_probe = self.query("decision promise fulfillment trust payment", mode="explore", max_results=5)
        compiled_probe = self.compiled_truth_status()
        if compiled_probe.get("status") in {"NO_PROJECTION", "STALE"}:
            compiled_probe = self.compiled_truth_build()
        graph_probe = self.graph_inspect(limit=5)
        graph_traverse_ok = index.get("sourceCount", 0) == 0
        if graph_probe.get("entities"):
            traversed = self.graph_traverse(str(graph_probe["entities"][0].get("name") or ""), depth=1, limit=10)
            graph_traverse_ok = bool(traversed.get("ok")) and (bool(traversed.get("citations")) or index.get("sourceCount", 0) == 0)
        synthetic = self._synthetic_eval_checks()
        checks = [
            self._score("knowledge_index_fresh", bool(index.get("fresh")), "Knowledge index pins current ledger state."),
            self._score("graph_evidence_locked", graph.get("uncitedActiveItems") == 0, "No active entity or edge lacks citation/evidence hash."),
            self._score("compiled_truth_projection_fresh", bool(compiled_probe.get("ok")) and compiled_probe.get("status") == "PASS", "Compiled-truth projection is fresh and derived from the current index."),
            self._score("graph_inspect_traverse", bool(graph_probe.get("ok")) and graph_traverse_ok, "Graph inspect/traverse returns cited evidence when graph evidence exists."),
            self._score("report_feedback_fenced", report_exclusion, "Generated reports are not indexed as source memory."),
            self._score("query_returns_citations", bool(query_probe.get("citations")) or index.get("sourceCount", 0) == 0, "Knowledge query emits citations when evidence exists."),
            self._score("synthesis_is_derived", synthesis.get("status") in {"PASS", "NO_SYNTHESIS"}, "Synthesis is derived/provisional and separate from ledger authority."),
            *synthetic,
        ]
        score = round(sum(item["score"] for item in checks) / max(len(checks), 1), 2)
        payload = {
            "ok": all(item["ok"] for item in checks),
            "status": "PASS" if all(item["ok"] for item in checks) else "DEGRADED",
            "schema": EVAL_SCHEMA_VERSION,
            "created_at": utc_now(),
            "score": score,
            "checks": checks,
            "fixtureCount": len(synthetic),
            "releaseGate": {"stableV1": score >= 7 and all(item["ok"] for item in checks), "minimumScore": 7},
        }
        self._write_json(self.eval_dir / "latest.json", payload)
        return payload

    def evaluate_scorecard(self) -> Dict[str, Any]:
        latest = self.eval_dir / "latest.json"
        if not latest.exists():
            return {"ok": False, "status": "NO_EVAL", "error": "evaluation_not_found"}
        payload = json.loads(latest.read_text(encoding="utf-8"))
        return {"ok": bool(payload.get("ok")), "status": payload.get("status"), "scorecard": payload, "scorecardFile": str(latest)}

    def _external_provider_calls(
        self,
        providers: Iterable[str],
        *,
        scopes: Sequence[str],
        authorized: bool,
        redaction_count: int,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        calls: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for raw in providers:
            provider = _safe_id(str(raw or "").strip())
            if not provider:
                continue
            if not authorized:
                calls.append(
                    {
                        "provider": f"external:{provider}",
                        "local": False,
                        "scopesSent": [],
                        "redactionCount": redaction_count,
                        "authorization": "missing-explicit-external-provider-authorization",
                        "status": "SKIPPED",
                        "latencyMs": 0,
                    }
                )
                warnings.append("external_provider_requires_explicit_authorization")
                continue
            calls.append(
                {
                    "provider": f"external:{provider}",
                    "local": False,
                    "scopesSent": [scope for scope in scopes if scope != "private"],
                    "redactionCount": redaction_count,
                    "authorization": "explicit-external-provider-authorization-redacted-minimized",
                    "status": "UNAVAILABLE",
                    "error": "external_adapter_not_configured",
                    "latencyMs": 0,
                }
            )
            warnings.append("external_provider_unavailable")
        return calls, list(dict.fromkeys(warnings))

    def _federated_query(
        self,
        requested: Sequence[str],
        *,
        query: str,
        mode: str,
        session_id: str,
        max_results: int,
        at_time: str,
        scopes: Sequence[str],
        authorized: bool,
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "requested": list(requested),
            "authorized": bool(authorized),
            "merged": False,
            "workspaces": [],
            "status": "NOT_REQUESTED",
        }
        if not requested:
            return summary
        if not authorized:
            summary["status"] = "AUTHORIZATION_REQUIRED"
            summary["warning"] = "Federation was requested but not run because explicit authorization was not provided."
            return summary

        from .api import TotalRecallConfig, TotalRecallCore

        seen: set[str] = set()
        for raw in requested:
            workspace: Dict[str, Any] = {"requested": raw, "ok": False, "status": "DEGRADED"}
            try:
                home = self._resolve_federated_home(raw)
                resolved = str(home.resolve())
                workspace["home"] = str(home)
                workspace["homeHash"] = sha256_json({"home": resolved})
                if resolved in seen:
                    workspace["error"] = "duplicate_federated_workspace"
                    summary["workspaces"].append(workspace)
                    continue
                seen.add(resolved)
                if resolved == str(self.home.resolve()):
                    workspace["error"] = "cannot_federate_current_workspace"
                    summary["workspaces"].append(workspace)
                    continue
                if not (home / "ledger" / "events.jsonl").exists():
                    workspace["error"] = "federated_ledger_not_found"
                    summary["workspaces"].append(workspace)
                    continue
                foreign_core = TotalRecallCore(
                    TotalRecallConfig(
                        home=home,
                        allowed_scopes=tuple(scopes),
                        enable_lancedb=False,
                        enable_qmd=False,
                    )
                )
                result = KnowledgeEngine(foreign_core).query(
                    query,
                    mode=mode,
                    session_id=session_id,
                    max_results=max_results,
                    at_time=at_time,
                    allowed_scopes=scopes,
                    federate=[],
                    federation_authorized=False,
                    allow_rebuild=False,
                    audit=False,
                )
                workspace.update(
                    {
                        "ok": bool(result.get("ok")),
                        "status": result.get("status") or "PASS",
                        "answer": result.get("answer"),
                        "citations": result.get("citations") or [],
                        "evidence": result.get("evidence") or [],
                        "warnings": result.get("warnings") or [],
                    }
                )
                if result.get("error"):
                    workspace["error"] = result.get("error")
            except Exception as exc:
                workspace["error"] = str(exc)
            summary["workspaces"].append(workspace)
        summary["status"] = "PASS" if any(item.get("ok") for item in summary["workspaces"]) else "DEGRADED"
        return summary

    def _resolve_federated_home(self, raw: str) -> Path:
        path = Path(raw).expanduser()
        if (path / "ledger" / "events.jsonl").exists() or path.name == ".total-recall":
            return path
        if (path / ".total-recall" / "ledger" / "events.jsonl").exists():
            return path / ".total-recall"
        if (path / "total-recall" / "ledger" / "events.jsonl").exists():
            return path / "total-recall"
        return path

    def _write_provider_report(
        self,
        provider_calls: Sequence[Dict[str, Any]],
        *,
        query: str,
        mode: str,
        session_id: str,
        scopes: Sequence[str],
        at_time: str,
        federation: Dict[str, Any],
    ) -> Dict[str, Any]:
        self.ensure_layout()
        report_id = f"provider_report_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
        report_path = self.knowledge_dir / "providers" / f"{report_id}.json"
        sanitized_calls = []
        for call in provider_calls:
            provider = str(call.get("provider") or "unknown")
            sanitized_calls.append(
                {
                    "provider": provider,
                    "family": "rerank" if "rerank" in provider else "memory",
                    "local": bool(call.get("local")),
                    "external": not bool(call.get("local")),
                    "scopesSent": list(call.get("scopesSent") or []),
                    "redactionCount": int(call.get("redactionCount") or 0),
                    "authorization": str(call.get("authorization") or ""),
                    "status": str(call.get("status") or "UNKNOWN"),
                    "success": str(call.get("status") or "").upper() == "PASS",
                    "latencyMs": float(call.get("latencyMs") or 0),
                }
            )
        report = {
            "ok": True,
            "schema": "total-recall-knowledge-provider-report-v1",
            "report_id": report_id,
            "created_at": utc_now(),
            "query": {
                "sha256": sha256_json({"query": query}),
                "length": len(query),
                "mode": mode,
                "sessionIdHash": sha256_json({"session_id": session_id}) if session_id else None,
                "temporalAsOf": at_time or None,
            },
            "scopeFilter": {"allowedScopes": list(scopes), "filtered": True},
            "providerCalls": sanitized_calls,
            "federation": {
                "requestedCount": len(federation.get("requested") or []),
                "authorized": bool(federation.get("authorized")),
                "merged": bool(federation.get("merged")),
                "workspaceCount": len(federation.get("workspaces") or []),
                "workspaceStatuses": [
                    {
                        "homeHash": item.get("homeHash"),
                        "ok": bool(item.get("ok")),
                        "status": item.get("status"),
                        "citationCount": len(item.get("citations") or []),
                    }
                    for item in federation.get("workspaces") or []
                ],
            },
        }
        self._write_json(report_path, report)
        return {"reportId": report_id, "path": str(report_path), "sha256": self._file_sha256(report_path), "schema": report["schema"]}

    def _synthetic_eval_checks(self) -> List[Dict[str, Any]]:
        checks: List[Dict[str, Any]] = []
        from .api import TotalRecallConfig, TotalRecallCore

        try:
            with tempfile.TemporaryDirectory(prefix="total-recall-ke-eval-") as tmp:
                root = Path(tmp)
                home = root / "source"
                fed_home = root / "federated"
                core = TotalRecallCore(TotalRecallConfig(home=home, enable_lancedb=False, enable_qmd=False))
                core.ingest(kind="note", text="Private launch code alpha should stay private.", session_id="eval", scope="private")
                core.ingest(kind="note", text="Public storefront promise: returns are accepted for ten days.", session_id="eval", scope="public")
                core.ingest(kind="decision", text="Decision: fulfillment promise is ten-day delivery; this supersedes the old same-day promise.", session_id="eval", scope="public")
                core.ingest(kind="note", text="Payment policy uses tokenized checkout. API key sk-synthetic-secret-value must be redacted.", session_id="eval", scope="private")
                report = home / "reports" / "generated.json"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(canonical_json({"text": "Synthetic generated report poison phrase."}), encoding="utf-8")
                core.knowledge_index_rebuild()

                scope_probe = core.knowledge_query("private launch code alpha", mode="explore", allowed_scopes=["public"])
                scope_text = canonical_json(scope_probe.get("evidence") or []) + str(scope_probe.get("answer") or "")
                checks.append(self._score("fixture_scope_leak_public_only", "Private launch code" not in scope_text and not scope_probe.get("citations"), "Private scoped evidence is hidden from public-only queries."))

                conflict_probe = core.knowledge_query("fulfillment promise", mode="explore", allowed_scopes=["public"])
                checks.append(self._score("fixture_contradiction_warning", "possible_conflict_or_supersession_present" in (conflict_probe.get("warnings") or []), "Superseded promises produce a contradiction/supersession warning."))

                temporal_probe = core.knowledge_query("storefront promise", mode="explore", at_time="2000-01-01T00:00:00Z", allowed_scopes=["public", "private"])
                checks.append(self._score("fixture_temporal_as_of", temporal_probe.get("citations") == [], "As-of queries before the ledger event return no later evidence."))

                fenced_probe = core.knowledge_query("Synthetic generated report poison phrase", mode="explore", allowed_scopes=["public", "private"])
                fenced_text = canonical_json(fenced_probe.get("evidence") or [])
                checks.append(self._score("fixture_context_fencing_reports", fenced_probe.get("citations") == [] and "Synthetic generated report poison" not in fenced_text, "Generated reports are fenced from Knowledge Engine source recall."))

                provider_probe = core.knowledge_query("payment policy", mode="explore", allowed_scopes=["private"])
                report_info = provider_probe.get("providerReport") or {}
                provider_report = Path(str(report_info.get("path") or ""))
                report_text = provider_report.read_text(encoding="utf-8") if provider_report.exists() else ""
                checks.append(self._score("fixture_provider_payload_report", bool(report_info) and "sk-synthetic-secret-value" not in report_text and "Payment policy uses" not in report_text, "Provider reports are persisted without raw private memory payloads."))

                external_blocked = core.knowledge_query("payment policy", mode="explore", allowed_scopes=["private", "public"], external_providers=["hindsight"])
                external_allowed = core.knowledge_query("payment policy", mode="explore", allowed_scopes=["private", "public"], external_providers=["hindsight"], external_provider_authorized=True)
                blocked_call = (external_blocked.get("providerCalls") or [{}])[-1]
                allowed_call = (external_allowed.get("providerCalls") or [{}])[-1]
                checks.append(self._score("fixture_external_provider_auth_gate", blocked_call.get("status") == "SKIPPED" and blocked_call.get("scopesSent") == [] and allowed_call.get("status") == "UNAVAILABLE" and "private" not in (allowed_call.get("scopesSent") or []), "External provider adapters require explicit authorization and degrade without private payloads."))

                for fixture in self._redacted_hermes_fixture_events():
                    core.ingest(
                        kind=str(fixture.get("kind") or "hermes_turn"),
                        text=str(fixture.get("text") or ""),
                        session_id=str(fixture.get("session_id") or "hermes-smoke"),
                        scope=str(fixture.get("scope") or "private"),
                        metadata={"fixture": "redacted-hermes-smoke", **(fixture.get("metadata") or {})},
                    )
                core.knowledge_index_rebuild()
                hermes_probe = core.knowledge_query("brand voice", mode="explore", session_id="hermes-smoke", allowed_scopes=["private"])
                hermes_report = Path(str((hermes_probe.get("providerReport") or {}).get("path") or ""))
                hermes_report_text = hermes_report.read_text(encoding="utf-8") if hermes_report.exists() else ""
                hermes_evidence = canonical_json(hermes_probe.get("evidence") or [])
                checks.append(self._score("fixture_redacted_hermes_smoke", bool(hermes_probe.get("citations")) and "founder@example.test" not in hermes_evidence and "sk-hermes-fixture-secret" not in hermes_evidence and "founder@example.test" not in hermes_report_text and "sk-hermes-fixture-secret" not in hermes_report_text, "Redacted Hermes smoke fixture remains queryable with citations while secrets stay out of evidence/report payloads."))

                fed_core = TotalRecallCore(TotalRecallConfig(home=fed_home, enable_lancedb=False, enable_qmd=False))
                fed_core.ingest(kind="note", text="Federated workspace return policy is thirty days.", session_id="eval", scope="public")
                fed_core.knowledge_index_rebuild()
                blocked_fed = core.knowledge_query("return policy", mode="explore", allowed_scopes=["public"], federate=[str(fed_home)])
                allowed_fed = core.knowledge_query("return policy", mode="explore", allowed_scopes=["public"], federate=[str(fed_home)], federation_authorized=True)
                fed_workspace = (allowed_fed.get("federation") or {}).get("workspaces") or []
                checks.append(self._score("fixture_federation_authorization", (blocked_fed.get("federation") or {}).get("status") == "AUTHORIZATION_REQUIRED" and not ((blocked_fed.get("federation") or {}).get("workspaces") or []), "Federation does nothing without explicit authorization."))
                checks.append(self._score("fixture_federation_workspace_separated", bool(fed_workspace and fed_workspace[0].get("citations")) and (allowed_fed.get("federation") or {}).get("merged") is False, "Authorized federation returns workspace-separated cited results without silent merge."))
        except Exception as exc:
            checks.append(self._score("fixture_harness_exception", False, f"Synthetic evaluation fixture failed: {exc}"))
        return checks

    def _redacted_hermes_fixture_events(self) -> List[Dict[str, Any]]:
        fallback = [
            {
                "kind": "hermes_turn",
                "session_id": "hermes-smoke",
                "scope": "private",
                "text": "Hermes user asked to remember the brand voice. Contact email founder@example.test and token sk-hermes-fixture-secret must be redacted.",
                "metadata": {"fixture": "inline-fallback"},
            }
        ]
        try:
            fixture_file = resources.files("total_recall_core").joinpath("fixtures/hermes_smoke_redacted.jsonl")
            text = fixture_file.read_text(encoding="utf-8")
        except Exception:
            return fallback
        rows: List[Dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if item.get("text"):
                rows.append(item)
        return rows or fallback

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("CREATE TABLE knowledge_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            """
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                timestamp TEXT,
                effective_timestamp TEXT,
                session_id TEXT,
                scope TEXT NOT NULL,
                source_ref TEXT NOT NULL UNIQUE,
                text TEXT NOT NULL,
                sanitized_text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                redaction_count INTEGER NOT NULL,
                injection_flags_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE VIRTUAL TABLE sources_fts USING fts5(sanitized_text, content='sources', content_rowid='id')")
        conn.execute("CREATE INDEX sources_scope_idx ON sources(scope)")
        conn.execute("CREATE INDEX sources_session_idx ON sources(session_id)")
        conn.execute("CREATE INDEX sources_timestamp_idx ON sources(timestamp)")
        conn.execute(
            """
            CREATE TABLE entities (
                entity_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                scope TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX entities_normalized_idx ON entities(normalized_name)")
        conn.execute(
            """
            CREATE TABLE edges (
                edge_id TEXT PRIMARY KEY,
                source_entity_id TEXT NOT NULL,
                target_entity_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                scope TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX edges_source_ref_idx ON edges(source_ref)")
        conn.execute("CREATE TABLE quarantine (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, source_ref TEXT NOT NULL, evidence_hash TEXT NOT NULL, reason TEXT NOT NULL, payload_json TEXT NOT NULL)")

    def _sanitize(self, text: str) -> Tuple[str, int, List[str]]:
        redactions = 0
        sanitized = text
        for pattern in _SECRET_PATTERNS:
            sanitized, count = pattern.subn(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", sanitized)
            redactions += count
        lowered = sanitized.lower()
        flags = [pattern for pattern in _INJECTION_PATTERNS if pattern in lowered]
        return sanitized, redactions, flags

    def _extract_graph(self, event: Dict[str, Any], text: str, source_ref: str, evidence_hash: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        entities: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []
        quarantined: List[Dict[str, Any]] = []

        def add_entity(name: str, type_: str, confidence: float = 0.75) -> Dict[str, Any]:
            normalized = self._normalize_entity(name)
            entity_id = f"ent_{hashlib_hash(type_ + ':' + normalized)}"
            entity = {
                "entity_id": entity_id,
                "name": name.strip()[:160],
                "normalized_name": normalized,
                "type": type_,
                "confidence": confidence,
            }
            if not normalized:
                quarantined.append({"kind": "entity", "reason": "empty_normalized_name", "payload": entity})
            else:
                entities[entity_id] = entity
            return entity

        session_entity = add_entity(str(event.get("session_id") or "default"), "session", 0.95)
        for path in sorted(set(re.findall(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+", text))):
            target = add_entity(path, "file", 0.9)
            edges.append(self._edge(session_entity, target, "references", source_ref, evidence_hash, 0.85))
        lowered_tokens = [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text)]
        chosen = []
        for token in lowered_tokens:
            if token in _STOPWORDS or token in chosen:
                continue
            chosen.append(token)
            if len(chosen) >= 10:
                break
        for token in chosen:
            type_ = _ENTITY_TYPES.get(token, "concept")
            target = add_entity(token, type_, 0.65 if type_ == "concept" else 0.8)
            edges.append(self._edge(session_entity, target, "about", source_ref, evidence_hash, 0.7))
        if self._looks_decision(text):
            target = add_entity(self._first_sentence(text), "decision", 0.85)
            edges.append(self._edge(session_entity, target, "decided", source_ref, evidence_hash, 0.82))
        if self._looks_conflicting(text):
            target = add_entity(self._first_sentence(text), "decision", 0.8)
            edges.append(self._edge(session_entity, target, "supersedes", source_ref, evidence_hash, 0.75))
        deduped_edges = {edge["edge_id"]: edge for edge in edges if edge.get("source_entity_id") and edge.get("target_entity_id")}
        return list(entities.values()), list(deduped_edges.values()), quarantined

    def _edge(self, source: Dict[str, Any], target: Dict[str, Any], relation: str, source_ref: str, evidence_hash: str, confidence: float) -> Dict[str, Any]:
        payload = f"{source.get('entity_id')}:{relation}:{target.get('entity_id')}:{source_ref}"
        return {
            "edge_id": f"edge_{hashlib_hash(payload)}",
            "source_entity_id": str(source.get("entity_id") or ""),
            "target_entity_id": str(target.get("entity_id") or ""),
            "relation": relation,
            "confidence": confidence,
        }

    def _query_candidates(
        self,
        query: str,
        *,
        mode: str,
        session_id: str,
        max_results: int,
        at_time: str,
        allowed_scopes: Sequence[str],
    ) -> List[Dict[str, Any]]:
        tokens = self._tokens(query)
        scopes = list(allowed_scopes)
        with sqlite3.connect(self.index_file) as conn:
            conn.row_factory = sqlite3.Row
            merged: Dict[str, Dict[str, Any]] = {}
            self._merge_source_rows(merged, self._fts_rows(conn, query, scopes=scopes, session_id=session_id, at_time=at_time, limit=max_results * 4), "fts")
            self._merge_source_rows(merged, self._lexical_rows(conn, tokens, scopes=scopes, session_id=session_id, at_time=at_time, limit=max_results * 4), "lexical")
            graph_refs = self._graph_source_refs(conn, tokens, scopes=scopes, session_id=session_id, at_time=at_time, limit=max_results * 4)
            if graph_refs:
                self._merge_source_rows(merged, self._rows_for_source_refs(conn, graph_refs), "graph")
        candidates = list(merged.values())
        query_vector = self.core._embed_text(query)
        for item in candidates:
            local_score = self._cosine(query_vector, self.core._embed_text(str(item.get("sanitized_text") or item.get("text") or "")))
            item["score"] = round(float(item.get("score") or 0) + local_score + self._recency_boost(str(item.get("effective_timestamp") or item.get("timestamp") or "")), 6)
            if self._looks_conflicting(str(item.get("text") or "")):
                item.setdefault("signals", []).append("possible_conflict_or_supersession")
        candidates.sort(key=lambda x: (float(x.get("score") or 0), x.get("effective_timestamp") or x.get("timestamp") or ""), reverse=True)
        limit = max(1, int(max_results))
        return [self._public_source(item) for item in candidates[:limit]]

    def _fts_rows(self, conn: sqlite3.Connection, query: str, *, scopes: Sequence[str], session_id: str, at_time: str, limit: int) -> List[sqlite3.Row]:
        tokens = self._tokens(query)
        if not tokens:
            return []
        match_expr = " ".join(f'"{token}"' for token in tokens)
        where, params = self._where(scopes=scopes, session_id=session_id, at_time=at_time)
        return conn.execute(
            f"""
            SELECT s.*, -bm25(sources_fts) AS rank_score
            FROM sources_fts
            JOIN sources s ON s.id = sources_fts.rowid
            WHERE sources_fts MATCH ? AND {where}
            ORDER BY rank_score DESC, COALESCE(s.effective_timestamp, s.timestamp) DESC
            LIMIT ?
            """,
            [match_expr, *params, max(1, limit)],
        ).fetchall()

    def _lexical_rows(self, conn: sqlite3.Connection, tokens: Sequence[str], *, scopes: Sequence[str], session_id: str, at_time: str, limit: int) -> List[sqlite3.Row]:
        where, params = self._where(scopes=scopes, session_id=session_id, at_time=at_time)
        rows = conn.execute(f"SELECT s.*, 0.0 AS rank_score FROM sources s WHERE {where} ORDER BY COALESCE(s.effective_timestamp, s.timestamp) DESC LIMIT ?", [*params, 500]).fetchall()
        scored = []
        for row in rows:
            text = str(row["sanitized_text"] or "").lower()
            score = sum(1 for token in tokens if token in text)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["effective_timestamp"] or item[1]["timestamp"] or ""), reverse=True)
        return [row for _, row in scored[: max(1, limit)]]

    def _graph_source_refs(self, conn: sqlite3.Connection, tokens: Sequence[str], *, scopes: Sequence[str], session_id: str, at_time: str, limit: int) -> List[str]:
        if not tokens:
            return []
        scope_clause = ",".join("?" for _ in scopes)
        refs: List[str] = []
        for token in tokens:
            rows = conn.execute(
                f"""
                SELECT DISTINCT e.source_ref
                FROM entities e
                JOIN sources s ON s.source_ref = e.source_ref
                WHERE e.status = 'active'
                  AND e.scope IN ({scope_clause})
                  AND e.normalized_name LIKE ?
                  {"AND s.session_id = ?" if session_id else ""}
                  {"AND COALESCE(s.effective_timestamp, s.timestamp) <= ?" if at_time else ""}
                LIMIT ?
                """,
                [*scopes, f"%{token.lower()}%", *([session_id] if session_id else []), *([at_time] if at_time else []), max(1, limit)],
            ).fetchall()
            refs.extend(str(row[0]) for row in rows)
        return list(dict.fromkeys(refs))[: max(1, limit)]

    def _rows_for_source_refs(self, conn: sqlite3.Connection, refs: Sequence[str]) -> List[sqlite3.Row]:
        if not refs:
            return []
        placeholders = ",".join("?" for _ in refs)
        return conn.execute(f"SELECT s.*, 0.5 AS rank_score FROM sources s WHERE s.source_ref IN ({placeholders})", list(refs)).fetchall()

    def _merge_source_rows(self, merged: Dict[str, Dict[str, Any]], rows: Sequence[sqlite3.Row], strategy: str) -> None:
        for row in rows:
            source_ref = str(row["source_ref"])
            score = float(row["rank_score"] or 0)
            if source_ref not in merged:
                merged[source_ref] = dict(row)
                merged[source_ref]["score"] = score
                merged[source_ref]["strategies"] = [strategy]
            else:
                merged[source_ref]["score"] = float(merged[source_ref].get("score") or 0) + score
                merged[source_ref].setdefault("strategies", []).append(strategy)

    def _where(self, *, scopes: Sequence[str], session_id: str, at_time: str) -> Tuple[str, List[Any]]:
        scope_clause = ",".join("?" for _ in scopes)
        clauses = [f"s.scope IN ({scope_clause})"]
        params: List[Any] = list(scopes)
        if session_id:
            clauses.append("s.session_id = ?")
            params.append(session_id)
        if at_time:
            clauses.append("COALESCE(s.effective_timestamp, s.timestamp) <= ?")
            params.append(at_time)
        return " AND ".join(clauses), params

    def _graph_context(self, source_refs: Sequence[Any], *, scopes: Sequence[str]) -> Dict[str, Any]:
        refs = [str(ref) for ref in source_refs if ref]
        if not refs or not self.index_file.exists():
            return {"entities": [], "edges": []}
        placeholders = ",".join("?" for _ in refs)
        scope_clause = ",".join("?" for _ in scopes)
        with sqlite3.connect(self.index_file) as conn:
            conn.row_factory = sqlite3.Row
            entities = [dict(row) for row in conn.execute(f"SELECT entity_id, name, type, source_ref, evidence_hash, confidence FROM entities WHERE status = 'active' AND source_ref IN ({placeholders}) AND scope IN ({scope_clause}) LIMIT 40", [*refs, *scopes]).fetchall()]
            edges = [dict(row) for row in conn.execute(f"SELECT edge_id, source_entity_id, target_entity_id, relation, source_ref, evidence_hash, confidence FROM edges WHERE status = 'active' AND source_ref IN ({placeholders}) AND scope IN ({scope_clause}) LIMIT 60", [*refs, *scopes]).fetchall()]
        return {"entities": entities, "edges": edges}

    def _top_sources(self, *, limit: int) -> List[Dict[str, Any]]:
        if not self.index_file.exists():
            return []
        with sqlite3.connect(self.index_file) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM sources ORDER BY COALESCE(effective_timestamp, timestamp) DESC LIMIT ?", (max(1, limit),)).fetchall()
        return [self._public_source(dict(row)) for row in rows]

    def _entity_rows(self, *, limit: int) -> List[Dict[str, Any]]:
        if not self.index_file.exists():
            return []
        with sqlite3.connect(self.index_file) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT entity_id, name, type, source_ref, evidence_hash, confidence FROM entities WHERE status = 'active' ORDER BY confidence DESC, created_at DESC LIMIT ?", (max(1, limit),)).fetchall()
        return [dict(row) for row in rows]

    def _sources_for_refs(self, conn: sqlite3.Connection, refs: Sequence[str]) -> List[Dict[str, Any]]:
        refs = [str(ref) for ref in refs if ref]
        if not refs:
            return []
        placeholders = ",".join("?" for _ in refs)
        rows = conn.execute(f"SELECT * FROM sources WHERE source_ref IN ({placeholders}) ORDER BY COALESCE(effective_timestamp, timestamp) DESC", refs).fetchall()
        return [self._public_source(dict(row)) for row in rows]

    def _compiled_items(self, sources: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for source in sources:
            text = str(source.get("text") or "").replace("\n", " ").strip()
            items.append(
                {
                    "text": text[:500],
                    "timestamp": source.get("timestamp"),
                    "kind": source.get("kind"),
                    "scope": source.get("scope"),
                    "source_ref": source.get("source_ref"),
                    "evidence_hash": source.get("evidence_hash"),
                    "citation": self._citation(source),
                }
            )
        return items

    def _compiled_truth_markdown(self, payload: Dict[str, Any]) -> str:
        sections = payload.get("sections") or {}
        lines = [
            "# Total Recall Compiled Truth",
            "",
            "This is a derived, human-readable projection. The append-only ledger, checkpoints, and signed anchors remain the authority.",
            "",
            f"- Schema: `{payload.get('schema')}`",
            f"- Projection hash: `{payload.get('projectionHash')}`",
            f"- Index state hash: `{payload.get('indexStateHash')}`",
            f"- Source count: `{payload.get('sourceCount')}`",
            "",
        ]
        for title, key in (("Decisions", "decisions"), ("Promises", "promises"), ("Tasks", "tasks")):
            lines.extend([f"## {title}", ""])
            items = sections.get(key) or []
            if not items:
                lines.append("- No cited items found.")
            for item in items[:20]:
                lines.append(f"- {item.get('text')} [`{item.get('source_ref')}` / `{item.get('evidence_hash')}`]")
            lines.append("")
        lines.extend(["## Entity Highlights", ""])
        entities = sections.get("entities") or []
        if not entities:
            lines.append("- No active graph entities found.")
        for entity in entities[:30]:
            lines.append(f"- {entity.get('name')} ({entity.get('type')}) [`{entity.get('source_ref')}` / `{entity.get('evidence_hash')}`]")
        lines.extend(["", "## Timeline", ""])
        for item in (sections.get("timeline") or [])[:20]:
            lines.append(f"- {item.get('timestamp')}: {item.get('text')} [`{item.get('source_ref')}`]")
        return "\n".join(lines) + "\n"

    def _public_source(self, item: Dict[str, Any]) -> Dict[str, Any]:
        metadata = {}
        try:
            metadata = json.loads(item.get("metadata_json") or "{}") if isinstance(item.get("metadata_json"), str) else item.get("metadata") or {}
        except Exception:
            metadata = {}
        flags = []
        try:
            flags = json.loads(item.get("injection_flags_json") or "[]")
        except Exception:
            flags = []
        return {
            "kind": item.get("kind"),
            "id": item.get("event_id") or item.get("id"),
            "timestamp": item.get("timestamp"),
            "effective_timestamp": item.get("effective_timestamp") or item.get("timestamp"),
            "session_id": item.get("session_id"),
            "scope": item.get("scope"),
            "text": item.get("sanitized_text") or item.get("text") or "",
            "source_ref": item.get("source_ref"),
            "evidence_hash": item.get("evidence_hash"),
            "text_hash": item.get("text_hash"),
            "metadata": metadata,
            "score": item.get("score", 0),
            "strategies": item.get("strategies", []),
            "redaction_count": item.get("redaction_count", 0),
            "injection_flags": flags,
            "signals": item.get("signals", []),
        }

    def _freshness_for_sources(
        self,
        sources: Sequence[Dict[str, Any]],
        *,
        scopes: Sequence[str],
        at_time: str,
    ) -> Dict[str, Any]:
        refs = {str(source.get("source_ref") or "") for source in sources if source.get("source_ref")}
        if not refs:
            return {"status": "NO_EVIDENCE", "items": [], "counts": {}}
        as_of = self._parse_time(at_time) if at_time else datetime.now(timezone.utc)
        items = [item for item in self._freshness_items(scopes=scopes, as_of=as_of) if item.get("source_ref") in refs]
        counts: Dict[str, int] = {}
        for item in items:
            counts[item["freshness"]] = counts.get(item["freshness"], 0) + 1
        return {"status": "PASS", "asOf": as_of.isoformat().replace("+00:00", "Z"), "counts": counts, "items": items}

    def _freshness_items(
        self,
        *,
        scopes: Sequence[str],
        as_of: datetime,
        entity: str = "",
        category: str = "",
    ) -> List[Dict[str, Any]]:
        if not self.index_file.exists():
            return []
        entity_needle = self._normalize_entity(entity)
        category_filter = _safe_id(category).replace("_", "-") if category else ""
        with sqlite3.connect(self.index_file) as conn:
            conn.row_factory = sqlite3.Row
            scope_clause = ",".join("?" for _ in scopes)
            rows = [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM sources WHERE scope IN ({scope_clause}) ORDER BY COALESCE(effective_timestamp, timestamp) ASC",
                    list(scopes),
                ).fetchall()
            ]
        sources = [self._public_source(row) for row in rows]
        keyed: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for source in sources:
            source_time = self._parse_time(str(source.get("effective_timestamp") or source.get("timestamp") or ""))
            if source_time and source_time > as_of:
                continue
            cat = self._freshness_category(source)
            subject = self._freshness_subject(source)
            if category_filter and cat != category_filter:
                continue
            if entity_needle and entity_needle not in self._normalize_entity(subject + " " + str(source.get("text") or "")):
                continue
            keyed.setdefault((cat, subject), []).append(source)
        superseded_refs: set[str] = set()
        for entries in keyed.values():
            previous = []
            for source in entries:
                metadata = source.get("metadata") or {}
                explicit = metadata.get("supersedes_source_ref") or metadata.get("supersedes")
                if isinstance(explicit, str):
                    superseded_refs.add(explicit if explicit.startswith("ledger:") else f"ledger:{explicit}")
                elif isinstance(explicit, list):
                    for ref in explicit:
                        superseded_refs.add(str(ref) if str(ref).startswith("ledger:") else f"ledger:{ref}")
                if self._looks_conflicting(str(source.get("text") or "")):
                    superseded_refs.update(str(item.get("source_ref")) for item in previous if item.get("source_ref"))
                previous.append(source)
        latest_by_key = {key: max(entries, key=lambda item: str(item.get("effective_timestamp") or item.get("timestamp") or "")) for key, entries in keyed.items()}
        items: List[Dict[str, Any]] = []
        for key, entries in keyed.items():
            latest_ref = str(latest_by_key[key].get("source_ref") or "")
            for source in entries:
                record = self._freshness_record_for_source(source, as_of=as_of)
                source_ref = str(source.get("source_ref") or "")
                if source_ref in superseded_refs or (source_ref != latest_ref and record["category"] in {"promise", "decision", "policy", "project-state", "customer"}):
                    record["freshness"] = "superseded"
                    record.setdefault("reasons", []).append("newer_same_subject_or_explicit_supersession")
                items.append(record)
        order = {"stale": 0, "superseded": 1, "review_needed": 2, "current": 3}
        items.sort(key=lambda item: (order.get(item.get("freshness"), 9), item.get("category") or "", item.get("subject") or "", item.get("timestamp") or ""))
        return items

    def _freshness_record_for_source(self, source: Dict[str, Any], *, as_of: Optional[datetime]) -> Dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        category = self._freshness_category(source)
        subject = self._freshness_subject(source)
        metadata = source.get("metadata") or {}
        reasons: List[str] = []
        freshness = "current"
        expires_at = self._parse_time(str(metadata.get("expires_at") or metadata.get("valid_to") or ""))
        effective_timestamp = source.get("effective_timestamp") or source.get("timestamp")
        timestamp = self._parse_time(str(effective_timestamp or ""))
        thresholds = {
            "promise": 60,
            "decision": 365,
            "customer": 90,
            "policy": 180,
            "project-state": 45,
            "task": 30,
        }
        if expires_at and expires_at <= as_of:
            freshness = "stale"
            reasons.append("expired")
        elif timestamp:
            age_days = (as_of - timestamp).total_seconds() / 86400
            threshold = thresholds.get(category)
            if threshold is not None and age_days > threshold:
                freshness = "stale"
                reasons.append(f"older_than_{threshold}_days")
        if self._looks_conflicting(str(source.get("text") or "")):
            reasons.append("contains_supersession_language")
        if not reasons:
            reasons.append("latest_cited_memory")
        return {
            "source_ref": source.get("source_ref"),
            "evidence_hash": source.get("evidence_hash"),
            "timestamp": effective_timestamp,
            "ledger_timestamp": source.get("timestamp"),
            "category": category,
            "subject": subject,
            "freshness": freshness,
            "reasons": reasons,
            "text": str(source.get("text") or "")[:500],
            "citation": self._citation(source),
        }

    def _freshness_category(self, source: Dict[str, Any]) -> str:
        metadata = source.get("metadata") or {}
        explicit = str(metadata.get("freshness_category") or metadata.get("category") or "").strip().lower()
        if explicit:
            return _safe_id(explicit).replace("_", "-")
        kind = str(source.get("kind") or "").lower()
        text = str(source.get("text") or "").lower()
        if "customer" in kind or "customer" in text or "account" in text:
            return "customer"
        if "policy" in kind or "policy" in text:
            return "policy"
        if "project" in kind or "project state" in text or "status:" in text:
            return "project-state"
        if "promise" in kind or "promise" in text:
            return "promise"
        if self._looks_decision(str(source.get("text") or "")):
            return "decision"
        if self._looks_task(str(source.get("text") or "")):
            return "task"
        return "memory"

    def _freshness_subject(self, source: Dict[str, Any]) -> str:
        metadata = source.get("metadata") or {}
        for key in ("freshness_subject", "subject", "entity", "customer", "account", "project", "policy"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value[:160]
        text = str(source.get("text") or "")
        text_for_subject = re.sub(r"(?is)^Source type:.*?\n\s*\n", "", text, count=1).strip() or text
        match = re.search(r"(?im)^(?:subject|customer|account|project|policy|title):\s*(.+)$", text_for_subject)
        if match:
            return match.group(1).strip()[:160]
        first = self._first_sentence(text_for_subject)
        cleaned = re.sub(r"(?i)^(?:decision|promise|policy|project state|customer update|task|todo)\s*:\s*", "", first).strip()
        subject_match = re.match(
            r"(.{4,120}?)\s+(?:is|are|was|were|will|should|must|can|uses|use|has|have|=|:)\b",
            cleaned,
            flags=re.IGNORECASE,
        )
        if subject_match:
            return subject_match.group(1).strip(" .:-")[:160]
        colon_match = re.match(r"(.{4,120}?):\s+", cleaned)
        if colon_match:
            return colon_match.group(1).strip(" .:-")[:160]
        title = str(metadata.get("title") or "").strip()
        if title:
            return title[:160]
        return first[:120] or str(source.get("source_ref") or "memory")

    def _source_effective_timestamp(self, event: Dict[str, Any]) -> str:
        metadata = event.get("metadata") or {}
        for key in ("occurred_at", "effective_at", "valid_from", "meeting_at", "sent_at", "created_at"):
            parsed = self._parse_time(str(metadata.get(key) or ""))
            if parsed:
                return parsed.isoformat().replace("+00:00", "Z")
        return str(event.get("timestamp") or utc_now())

    def _parse_time(self, value: str) -> Optional[datetime]:
        value = str(value or "").strip()
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    def _citation(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_id": item.get("id"),
            "source_ref": item.get("source_ref"),
            "source_path": "ledger/events.jsonl",
            "timestamp": item.get("timestamp"),
            "scope": item.get("scope") or "private",
            "session_id": item.get("session_id") or "default",
            "trace_ref": None,
            "evidence_hash": item.get("evidence_hash"),
        }

    def _answer(self, query: str, candidates: Sequence[Dict[str, Any]], *, confidence: Dict[str, Any], refused: bool, at_time: str) -> str:
        if refused:
            return "Insufficient cited evidence to answer in strict mode."
        if not candidates:
            return "No matching cited Total Recall knowledge was found."
        top = candidates[0]
        prefix = f"As of {at_time}, " if at_time else ""
        text = str(top.get("text") or "").replace("\n", " ")[:360]
        return f"{prefix}found {len(candidates)} cited memory item(s) for '{query}'. Highest-confidence evidence: {text}"

    def _confidence(self, candidates: Sequence[Dict[str, Any]], *, mode: str) -> Dict[str, Any]:
        if not candidates:
            return {"level": "none", "score": 0.0, "reasons": ["no_cited_evidence"]}
        score = min(1.0, 0.25 + (len(candidates) * 0.12) + min(float(candidates[0].get("score") or 0), 0.5))
        if mode == "fast":
            score = min(1.0, score + 0.05)
        if mode == "strict" and len(candidates) < 2:
            score = min(score, 0.55)
        level = "high" if score >= 0.75 else "medium" if score >= 0.45 else "low"
        reasons = ["cited_evidence", "local_rerank"]
        if len(candidates) > 1:
            reasons.append("multiple_sources")
        return {"level": level, "score": round(score, 3), "reasons": reasons}

    def _synthesis_proposals(self, run_id: str, sources: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        proposals: List[Dict[str, Any]] = []
        decision_sources = [source for source in sources if self._looks_decision(str(source.get("text") or ""))]
        if decision_sources:
            proposals.append(
                {
                    "proposal_id": f"proposal_{hashlib_hash(run_id + ':decisions')}",
                    "type": "decision_summary",
                    "text": "Promote the latest cited decision summary if it remains accurate: " + str(decision_sources[0].get("text") or "")[:500],
                    "citations": [self._citation(source) for source in decision_sources[:5]],
                }
            )
        if sources:
            proposals.append(
                {
                    "proposal_id": f"proposal_{hashlib_hash(run_id + ':open-question')}",
                    "type": "open_question",
                    "text": "Review whether any stale or contradicted promise should be superseded before future Hermes answers.",
                    "citations": [self._citation(source) for source in sources[:5]],
                }
            )
        return proposals

    def _daily_brief_markdown(self, sources: Sequence[Dict[str, Any]], proposals: Sequence[Dict[str, Any]]) -> str:
        lines = ["# Total Recall Knowledge Synthesis", "", "This artifact is derived and provisional. Owner promotion is required before it becomes canonical memory.", "", "## Recent Evidence"]
        for source in sources[:10]:
            text = str(source.get("text") or "").replace("\n", " ")[:220]
            lines.append(f"- {text} [{source.get('source_ref')}]")
        lines.extend(["", "## Proposals"])
        for proposal in proposals:
            lines.append(f"- `{proposal.get('proposal_id')}` {proposal.get('text')}")
        return "\n".join(lines) + "\n"

    def _validate_synthesis(self, payload: Dict[str, Any], sources: Sequence[Dict[str, Any]]) -> None:
        for proposal in payload.get("proposals", []):
            if not proposal.get("citations"):
                raise ValueError(f"uncited synthesis proposal: {proposal.get('proposal_id')}")
        for source in sources:
            if not source.get("source_ref") or not source.get("evidence_hash"):
                raise ValueError("synthesis source without citation/evidence hash")

    def _write_graph_snapshot(self) -> None:
        self.graph_dir.mkdir(parents=True, exist_ok=True)
        snapshot = {"schema": "total-recall-knowledge-graph-snapshot-v1", "created_at": utc_now(), "status": self.graph_status(), "entities": self._entity_rows(limit=200)}
        self._write_json(self.graph_dir / "latest.json", snapshot)

    def _reports_excluded(self) -> bool:
        if not self.index_file.exists():
            return True
        with sqlite3.connect(self.index_file) as conn:
            count = conn.execute("SELECT COUNT(*) FROM sources WHERE source_ref LIKE ?", (f"{self.home / 'reports'}%",)).fetchone()[0]
        return int(count or 0) == 0

    def _score(self, name: str, ok: bool, summary: str) -> Dict[str, Any]:
        return {"name": name, "ok": ok, "score": 10 if ok else 0, "summary": summary}

    def _normalize_entity(self, value: str) -> str:
        return " ".join(re.findall(r"[a-z0-9_.-]+", value.lower()))[:160]

    def _tokens(self, text: str) -> List[str]:
        return [token.lower() for token in re.findall(r"[\w.-]+", text or "") if token.strip() and token.lower() not in _STOPWORDS]

    def _first_sentence(self, text: str) -> str:
        return re.split(r"(?<=[.!?])\s+", text.strip())[0][:180] or text.strip()[:180]

    def _looks_decision(self, text: str) -> bool:
        lowered = text.lower()
        return any(word in lowered for word in ("decision", "decided", "must", "should", "promise", "supersedes", "owner-only", "do not"))

    def _looks_task(self, text: str) -> bool:
        lowered = text.lower()
        return any(word in lowered for word in ("todo", "next", "implement", "fix", "blocker", "follow up", "follow-up", "should"))

    def _looks_conflicting(self, text: str) -> bool:
        lowered = text.lower()
        return any(word in lowered for word in ("supersedes", "contradicts", "instead of", "no longer", "do not promise", "old"))

    def _recency_boost(self, timestamp: str) -> float:
        if not timestamp:
            return 0.0
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            age_days = max((datetime.now(timezone.utc) - dt).total_seconds() / 86400, 0)
            return max(0.0, 0.1 - min(age_days, 30) / 300)
        except Exception:
            return 0.0

    def _cosine(self, a: Sequence[float], b: Sequence[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    def _evidence_hash(self, *, source_ref: str, text_hash: str, event_hash: str) -> str:
        return sha256_json({"source_ref": source_ref, "text_hash": text_hash, "event_hash": event_hash})

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        self.core._write_json(path, payload)

    def _file_sha256(self, path: Path) -> str:
        return self.core._file_sha256(path)


def hashlib_hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
