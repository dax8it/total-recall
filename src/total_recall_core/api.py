from __future__ import annotations

import fnmatch
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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "1.3.0"
SIGNING_ALGORITHM = "ed25519-local-v1"
LEGACY_SIGNING_ALGORITHM = "hmac-sha256-local-v1"
INDEX_SCHEMA_VERSION = "total-recall-sqlite-fts-v1"
LANCEDB_INDEX_SCHEMA_VERSION = "total-recall-lancedb-derived-v1"
QMD_INDEX_SCHEMA_VERSION = "total-recall-qmd-derived-v1"
EMBEDDING_DIMENSIONS = 128
DOCUMENT_INGEST_SCHEMA_VERSION = "total-recall-document-ingest-v1"
OBSIDIAN_VAULT_SCHEMA_VERSION = "total-recall-obsidian-vault-v1"
WORKING_CONTEXT_SCHEMA_VERSION = "total-recall-working-context-source-v1"
OBSIDIAN_IMPORT_SCHEMA_VERSION = "total-recall-obsidian-import-review-v1"
LEARNING_REVIEW_SCHEMA_VERSION = "total-recall-learning-review-v1"
FEDERATION_SCHEMA_VERSION = "total-recall-federation-registry-v1"
TRUST_GATE_SCHEMA_VERSION = "total-recall-trust-gate-v1"
TRUST_GATE_REQUIRED_HERMES_TOOLS = {
    "total_recall_search",
    "total_recall_status",
    "total_recall_checkpoint",
    "total_recall_verify",
    "total_recall_trust_verify",
    "total_recall_learning_review",
    "total_recall_rehydrate",
    "total_recall_incidents",
    "total_recall_source_ingest",
    "total_recall_knowledge_query",
    "total_recall_knowledge_freshness",
    "total_recall_knowledge_status",
    "total_recall_knowledge_synthesis_status",
    "total_recall_knowledge_compiled_truth",
    "total_recall_knowledge_graph_inspect",
    "total_recall_knowledge_graph_timeline",
    "total_recall_federation_query",
}
DEFAULT_DOCUMENT_EXTENSIONS = {
    ".adoc",
    ".cfg",
    ".conf",
    ".csv",
    ".htm",
    ".html",
    ".ini",
    ".json",
    ".jsonl",
    ".md",
    ".mdown",
    ".markdown",
    ".rst",
    ".text",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
DEFAULT_DOCUMENT_EXCLUDE_GLOBS = {
    ".DS_Store",
    "*.pyc",
    "*.pyo",
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}
WORKING_CONTEXT_SOURCE_TYPES = {
    "agent_transcript",
    "calendar",
    "crm",
    "email",
    "github",
    "meeting",
    "slack",
    "ticket",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _safe_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip())
    return cleaned.strip("._") or "default"


def _normalize_extensions(values: Iterable[str]) -> set[str]:
    normalized = set()
    for value in values:
        item = str(value or "").strip().lower()
        if not item:
            continue
        normalized.add(item if item.startswith(".") else f".{item}")
    return normalized


def _display_document_path(path: Path, *, root: Path) -> str:
    resolved = path.expanduser()
    try:
        return str(resolved.resolve().relative_to(Path.cwd().resolve()))
    except Exception:
        pass
    try:
        rel = resolved.relative_to(root)
        return str(Path(root.name) / rel) if root.name else str(rel)
    except Exception:
        return resolved.name


def _is_ignored_document_path(path: Path, *, root: Path, exclude_globs: set[str]) -> bool:
    try:
        rel = path.relative_to(root)
    except Exception:
        rel = Path(path.name)
    parts = rel.parts
    if any(part.startswith(".") for part in parts):
        return True
    rel_text = str(rel)
    for pattern in exclude_globs:
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
        if fnmatch.fnmatch(rel_text, pattern) or fnmatch.fnmatch(path.name, pattern):
            return True
    return False


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    if b"\x00" in raw:
        return True
    sample = raw[:4096]
    control = sum(1 for byte in sample if byte < 9 or (13 < byte < 32))
    return control / max(1, len(sample)) > 0.08


def _chunk_document_text(text: str, *, max_chars: int) -> List[str]:
    max_chars = max(1000, int(max_chars or 6000))
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    paragraphs = re.split(r"\n{2,}", text)
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if current:
                chunks.append("\n\n".join(current).strip())
                current = []
                current_len = 0
            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start : start + max_chars].strip())
            continue
        projected = current_len + len(paragraph) + (2 if current else 0)
        if current and projected > max_chars:
            chunks.append("\n\n".join(current).strip())
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len = projected
    if current:
        chunks.append("\n\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def _short_hash(value: str, length: int = 8) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[: max(4, int(length or 8))]


def _vault_slug(value: str) -> str:
    raw = str(value or "").strip()
    cleaned = re.sub(r"[/\\:|#^\[\]\r\n\t]+", "-", raw)
    cleaned = re.sub(r"[^A-Za-z0-9._ @()+='!-]+", "-", cleaned)
    cleaned = re.sub(r"\s+", "-", cleaned).strip(" .-_")
    if not cleaned:
        cleaned = "untitled"
    if len(cleaned) > 90:
        cleaned = f"{cleaned[:76].rstrip(' .-_')}-{_short_hash(raw)}"
    return cleaned


def _vault_frontmatter(payload: Dict[str, Any]) -> str:
    lines = ["---"]
    for key in sorted(payload):
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(key)):
            continue
        value = payload[key]
        if value is None:
            continue
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(["---", ""])
    return "\n".join(lines)


def _vault_link(page_no_ext: str, label: str = "") -> str:
    target = str(page_no_ext or "").replace("[", "(").replace("]", ")")
    safe_label = str(label or "").replace("|", "-").replace("[", "(").replace("]", ")")
    if safe_label and safe_label != target:
        return f"[[{target}|{safe_label}]]"
    return f"[[{target}]]"


def _fenced_text(text: str, *, language: str = "text") -> str:
    safe = str(text or "").replace("```", "`` `")
    return f"```{language}\n{safe}\n```"


def _split_markdown_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw = text[4:end].strip()
    body = text[end + 4 :].lstrip("\n")
    payload: Dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        try:
            payload[key] = json.loads(value)
        except Exception:
            payload[key] = value.strip("\"'")
    return payload, body


def _markdown_title(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
        if stripped:
            return _one_line(stripped, limit=90)
    return ""


def _one_line(text: str, *, limit: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) > limit:
        return cleaned[: max(1, limit - 3)].rstrip() + "..."
    return cleaned


def _event_source_ref(event: Dict[str, Any]) -> str:
    return f"ledger:{event.get('event_id')}"


def _event_title(event: Dict[str, Any]) -> str:
    metadata = event.get("metadata") or {}
    if metadata.get("document_path"):
        chunk = metadata.get("chunk_index")
        total = metadata.get("chunk_count")
        suffix = f" chunk {chunk}/{total}" if chunk and total else ""
        return f"{metadata.get('document_path')}{suffix}"
    text = str(event.get("text") or "").strip()
    first = next((line.strip("#:- ").strip() for line in text.splitlines() if line.strip()), "")
    return _one_line(first or str(event.get("source") or event.get("event_id") or "Memory"), limit=90)


def _event_summary(event: Dict[str, Any], *, limit: int = 240) -> str:
    return _one_line(str(event.get("text") or ""), limit=limit)


def _group_document_events(events: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        metadata = event.get("metadata") or {}
        doc_path = str(metadata.get("document_path") or "")
        if not doc_path:
            source = str(event.get("source") or "")
            doc_path = source.removeprefix("document:") if source.startswith("document:") else source
        if not doc_path:
            doc_path = "unknown-document"
        grouped.setdefault(doc_path, []).append(event)
    for items in grouped.values():
        items.sort(key=lambda item: int((item.get("metadata") or {}).get("chunk_index") or 0))
    return dict(sorted(grouped.items()))


def _group_events_by_day(events: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        day = str(event.get("timestamp") or "undated")[:10] or "undated"
        grouped.setdefault(day, []).append(event)
    return grouped


def _event_haystack(event: Dict[str, Any]) -> str:
    return " ".join(str(event.get(key) or "") for key in ("kind", "source", "text")).lower()


def _event_looks_decision(event: Dict[str, Any]) -> bool:
    haystack = _event_haystack(event)
    return str(event.get("kind") or "") == "decision" or any(
        marker in haystack for marker in ("decision:", "decided ", "decision ", "supersedes", "must ")
    )


def _event_looks_promise(event: Dict[str, Any]) -> bool:
    haystack = _event_haystack(event)
    return str(event.get("kind") or "") == "promise" or "promise" in haystack


def _event_looks_task(event: Dict[str, Any]) -> bool:
    haystack = _event_haystack(event)
    return str(event.get("kind") or "") in {"task", "todo"} or any(
        marker in haystack for marker in ("todo", "next action", "action item", "follow up", "implement ", "fix ")
    )


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
        default_factory=lambda: ("private", "group_safe", "internal", "shared_team", "public")
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
                "knowledgeEngine": "implemented-local-first-derived",
            },
        }

    def knowledge_status(self) -> Dict[str, Any]:
        return self._knowledge().status()

    def knowledge_index_status(self) -> Dict[str, Any]:
        return self._knowledge().index_status()

    def knowledge_index_rebuild(self) -> Dict[str, Any]:
        return self._knowledge().rebuild_index()

    def knowledge_graph_status(self) -> Dict[str, Any]:
        return self._knowledge().graph_status()

    def knowledge_graph_rebuild(self) -> Dict[str, Any]:
        return self._knowledge().rebuild_graph()

    def knowledge_graph_inspect(
        self,
        *,
        entity: str = "",
        source_ref: str = "",
        limit: int = 20,
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        return self._knowledge().graph_inspect(entity=entity, source_ref=source_ref, limit=limit, allowed_scopes=allowed_scopes)

    def knowledge_graph_traverse(
        self,
        entity: str,
        *,
        depth: int = 2,
        limit: int = 40,
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        return self._knowledge().graph_traverse(entity, depth=depth, limit=limit, allowed_scopes=allowed_scopes)

    def knowledge_graph_timeline(
        self,
        entity: str,
        *,
        at_time: str = "",
        limit: int = 40,
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        return self._knowledge().graph_timeline(entity, at_time=at_time, limit=limit, allowed_scopes=allowed_scopes)

    def knowledge_freshness_report(
        self,
        *,
        entity: str = "",
        category: str = "",
        at_time: str = "",
        allowed_scopes: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        return self._knowledge().freshness_report(entity=entity, category=category, at_time=at_time, allowed_scopes=allowed_scopes)

    def knowledge_compiled_truth_status(self) -> Dict[str, Any]:
        return self._knowledge().compiled_truth_status()

    def knowledge_compiled_truth_build(self) -> Dict[str, Any]:
        return self._knowledge().compiled_truth_build()

    def knowledge_compiled_truth_show(self, *, format_: str = "json") -> Dict[str, Any]:
        return self._knowledge().compiled_truth_show(format_=format_)

    def knowledge_query(
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
    ) -> Dict[str, Any]:
        return self._knowledge().query(
            query,
            mode=mode,
            session_id=session_id,
            max_results=max_results,
            at_time=at_time,
            allowed_scopes=allowed_scopes,
            federate=federate,
            federation_authorized=federation_authorized,
            external_providers=external_providers,
            external_provider_authorized=external_provider_authorized,
        )

    def knowledge_synthesize_status(self) -> Dict[str, Any]:
        return self._knowledge().synthesis_status()

    def knowledge_synthesize_run(self) -> Dict[str, Any]:
        return self._knowledge().synthesize_run()

    def knowledge_synthesize_promote(self, proposal_id: str, *, session_id: str = "default") -> Dict[str, Any]:
        return self._knowledge().synthesize_promote(proposal_id, session_id=session_id)

    def knowledge_evaluate_run(self) -> Dict[str, Any]:
        return self._knowledge().evaluate_run()

    def knowledge_evaluate_scorecard(self) -> Dict[str, Any]:
        return self._knowledge().evaluate_scorecard()

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

    def ingest_documents(
        self,
        paths: Sequence[str | Path],
        *,
        session_id: str = "documents",
        scope: str = "private",
        recursive: bool = True,
        include_extensions: Optional[Iterable[str]] = None,
        exclude_globs: Optional[Iterable[str]] = None,
        max_file_bytes: int = 2_000_000,
        chunk_chars: int = 6000,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        if scope not in self.config.allowed_scopes:
            raise ValueError(f"scope '{scope}' is not allowed")
        if not paths:
            raise ValueError("at least one document path is required")

        include = _normalize_extensions(include_extensions or DEFAULT_DOCUMENT_EXTENSIONS)
        excludes = set(DEFAULT_DOCUMENT_EXCLUDE_GLOBS)
        excludes.update(str(item) for item in exclude_globs or [])
        ingest_id = self._new_id("docingest")
        candidates = self._document_candidates(paths, recursive=recursive)
        files: List[Dict[str, Any]] = []
        events: List[Dict[str, Any]] = []

        for path, root, explicit in candidates:
            prepared = self._prepare_document_file(
                path,
                root=root,
                explicit=explicit,
                include_extensions=include,
                exclude_globs=excludes,
                max_file_bytes=max_file_bytes,
                chunk_chars=chunk_chars,
                ingest_id=ingest_id,
                session_id=session_id,
                scope=scope,
            )
            files.append(prepared["file"])
            events.extend(prepared.get("events") or [])

        if dry_run or not events:
            return {
                "ok": True,
                "status": "DRY_RUN" if dry_run else "NO_DOCUMENTS",
                "schema": DOCUMENT_INGEST_SCHEMA_VERSION,
                "ingestId": ingest_id,
                "dryRun": dry_run,
                "paths": [str(Path(item).expanduser()) for item in paths],
                "files": files,
                "fileCount": len(files),
                "ingestedFiles": sum(1 for item in files if item.get("status") == "planned"),
                "skippedFiles": sum(1 for item in files if item.get("status") == "skipped"),
                "chunkCount": len(events),
                "events": [],
            }

        with self._locked():
            previous_hash = self._last_event_hash()
            written_events = []
            for event_base in events:
                base = {**event_base, "prev_hash": previous_hash}
                event_hash = sha256_json(base)
                event = {**base, "hash": event_hash}
                with self.ledger_file.open("a", encoding="utf-8") as fh:
                    fh.write(canonical_json(event) + "\n")
                previous_hash = event_hash
                written_events.append(event)
            state = self.reduce_state(write=True)
            self._rebuild_index_locked(state=state, backends=("sqlite-fts",))

        by_source = {str(event.get("source")) for event in written_events}
        for item in files:
            if item.get("status") == "planned" and f"document:{item.get('documentPath')}" in by_source:
                item["status"] = "ingested"
        return {
            "ok": True,
            "status": "PASS",
            "schema": DOCUMENT_INGEST_SCHEMA_VERSION,
            "ingestId": ingest_id,
            "dryRun": False,
            "paths": [str(Path(item).expanduser()) for item in paths],
            "files": files,
            "fileCount": len(files),
            "ingestedFiles": sum(1 for item in files if item.get("status") == "ingested"),
            "skippedFiles": sum(1 for item in files if item.get("status") == "skipped"),
            "chunkCount": len(written_events),
            "events": written_events,
            "stateHash": state["state_hash"],
        }

    def ingest_source(
        self,
        *,
        source_type: str,
        text: str = "",
        file: str | Path | None = None,
        title: str = "",
        actor: str = "",
        occurred_at: str = "",
        participants: Optional[Iterable[str]] = None,
        session_id: str = "working-context",
        scope: str = "private",
        metadata: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        source_type = _safe_id(str(source_type or "").strip().lower().replace("-", "_"))
        if source_type not in WORKING_CONTEXT_SOURCE_TYPES:
            return {
                "ok": False,
                "status": "ERROR",
                "error": "unsupported_source_type",
                "sourceType": source_type,
                "supported": sorted(WORKING_CONTEXT_SOURCE_TYPES),
            }
        if scope not in self.config.allowed_scopes:
            raise ValueError(f"scope '{scope}' is not allowed")
        source_path: Optional[Path] = Path(file).expanduser() if file else None
        raw_bytes = b""
        if source_path:
            if not source_path.exists() or not source_path.is_file():
                return {"ok": False, "status": "ERROR", "error": "source_file_not_found", "path": str(source_path)}
            raw_bytes = source_path.read_bytes()
            if _looks_binary(raw_bytes):
                return {"ok": False, "status": "ERROR", "error": "binary_or_unsupported_text", "path": str(source_path)}
            text = raw_bytes.decode("utf-8-sig", errors="replace")
        text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return {"ok": False, "status": "ERROR", "error": "text_or_file_required"}
        participants_list = [str(item).strip() for item in participants or [] if str(item).strip()]
        source_id = self._new_id(f"source_{source_type}")
        title = title.strip() or (source_path.name if source_path else f"{source_type} source")
        payload_metadata = {
            "schema": WORKING_CONTEXT_SCHEMA_VERSION,
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "actor": actor,
            "occurred_at": occurred_at,
            "participants": participants_list,
            **(metadata or {}),
        }
        if source_path:
            payload_metadata.update(
                {
                    "source_file": str(source_path),
                    "source_file_name": source_path.name,
                    "source_file_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    "source_file_size_bytes": len(raw_bytes),
                }
            )
        lines = [
            f"Source type: {source_type}",
            f"Title: {title}",
        ]
        if occurred_at:
            lines.append(f"Occurred at: {occurred_at}")
        if actor:
            lines.append(f"Actor: {actor}")
        if participants_list:
            lines.append(f"Participants: {', '.join(participants_list)}")
        lines.extend(["", text])
        event_text = "\n".join(lines).strip()
        if dry_run:
            return {
                "ok": True,
                "status": "DRY_RUN",
                "schema": WORKING_CONTEXT_SCHEMA_VERSION,
                "sourceType": source_type,
                "title": title,
                "scope": scope,
                "sessionId": session_id,
                "textPreview": event_text[:1000],
                "metadata": payload_metadata,
            }
        ingested = self.ingest(
            kind=f"source_{source_type}",
            text=event_text,
            session_id=session_id or "working-context",
            scope=scope,
            source=f"{source_type}:{title}",
            metadata=payload_metadata,
        )
        return {
            "ok": True,
            "status": "PASS",
            "schema": WORKING_CONTEXT_SCHEMA_VERSION,
            "sourceType": source_type,
            "title": title,
            "event": ingested.get("event"),
            "stateHash": ingested.get("stateHash"),
        }

    def learning_review(
        self,
        *,
        session_id: str = "nightly-learning",
        since: str = "",
        limit: int = 80,
        allowed_scopes: Optional[Iterable[str]] = None,
        persist: bool = True,
    ) -> Dict[str, Any]:
        """Produce owner-reviewable memory candidate cards without mutating the ledger.

        This is the Total Recall equivalent of the article's gbrain nightly loop:
        candidate list, promotion decision, and wake-up diff. It deliberately
        writes only a review artifact under reviews/learning; durable memory
        still requires explicit ingest/promotion through ledgered APIs.
        """
        events = self._read_events(verify_chain=True)
        scopes = set(str(scope) for scope in (allowed_scopes or self.config.allowed_scopes))
        selected = []
        for event in events:
            if str(event.get("scope") or "") not in scopes:
                continue
            if since and str(event.get("timestamp") or "") <= since:
                continue
            kind = str(event.get("kind") or "")
            if kind in {"checkpoint", "trust_gate_report"}:
                continue
            selected.append(event)
        selected = selected[-max(1, int(limit or 80)) :]
        review_id = self._new_id("learning_review")
        candidates = [self._learning_candidate_card(event, review_id=review_id) for event in selected]
        layer_counts: Dict[str, int] = {}
        for candidate in candidates:
            layer_counts[candidate["layer"]] = layer_counts.get(candidate["layer"], 0) + 1
        wake_up_diff = [
            {
                "candidateId": candidate["candidate_id"],
                "layer": candidate["layer"],
                "targetPage": candidate["targetPage"],
                "whatChanged": candidate["whatChanged"],
                "compiledTruthAction": candidate["decision"]["compiledTruthAction"],
                "actionBoundary": candidate["actionBoundary"],
                "source": candidate["source"],
            }
            for candidate in candidates
            if candidate["layer"] in {"gbrain_page", "runtime_startup_rule", "open_loop"}
        ]
        payload: Dict[str, Any] = {
            "ok": True,
            "status": "PREVIEW",
            "schema": LEARNING_REVIEW_SCHEMA_VERSION,
            "review_id": review_id,
            "created_at": utc_now(),
            "session_id": session_id or "nightly-learning",
            "authority": "review-artifact-only-ledger-remains-authority",
            "sourceEventCount": len(selected),
            "candidateCount": len(candidates),
            "layerCounts": layer_counts,
            "candidates": candidates,
            "promotionDecisions": [candidate["decision"] for candidate in candidates],
            "wakeUpDiff": wake_up_diff,
            "reviewFile": None,
            "nextSteps": [
                "Review candidates before promotion; this command does not mutate the ledger.",
                "Promote runtime-only behavior rules to the runtime's own MEMORY/USER layer when appropriate.",
                "Promote cross-runtime object state with explicit ledger ingest, document ingest, source ingest, or vault import-promote.",
                "Put precise reminders in the scheduler/open-loop system rather than compiled truth.",
            ],
        }
        if persist:
            review_path = self.home / "reviews" / "learning" / f"{review_id}.json"
            self._write_json(review_path, payload)
            self._write_json(self.home / "reviews" / "learning" / "latest.json", payload)
            payload["reviewFile"] = str(review_path)
            self._write_json(review_path, payload)
            self._write_json(self.home / "reviews" / "learning" / "latest.json", payload)
        return payload

    def _learning_candidate_card(self, event: Dict[str, Any], *, review_id: str) -> Dict[str, Any]:
        text = str(event.get("text") or "")
        lower = text.lower()
        target_page, target_kind, target_name = self._learning_resolve_target(text, event)
        layer = self._learning_layer(text, target_kind=target_kind)
        if layer == "gbrain_page" and target_kind == "inbox":
            target_page = "inbox/needs-triage.md"
        elif layer == "runtime_startup_rule":
            target_page = f"runtime/{_vault_slug(str(event.get('session_id') or 'default')).lower()}.md"
        elif layer == "open_loop":
            target_page = f"open-loops/{_vault_slug(self._learning_subject(text, fallback=target_name)).lower()}.md"
        elif layer == "archive":
            target_page = "archive/reviewed-no-reuse-case.md"

        changes_top = any(marker in lower for marker in ("decision:", "now ", "current", "changed", "supersedes", "requires", "must "))
        compiled_action = "rewrite_top_half" if layer == "gbrain_page" and changes_top else ("append_timeline" if layer == "gbrain_page" else "none")
        decision = {
            "candidate_id": f"learn_{_short_hash(review_id + ':' + str(event.get('event_id')) + ':' + str(event.get('hash')), 12)}",
            "layer": layer,
            "targetPage": target_page,
            "promote": layer != "archive",
            "compiledTruthAction": compiled_action,
            "timelineAction": "append_evidence" if layer == "gbrain_page" else "none",
            "reason": self._learning_reason(layer, target_kind=target_kind, changes_top=changes_top),
        }
        action_boundary = self._learning_action_boundary(text, event)
        candidate = {
            "candidate_id": decision["candidate_id"],
            "source": {
                "source_ref": _event_source_ref(event),
                "event_id": event.get("event_id"),
                "event_hash": event.get("hash"),
                "timestamp": event.get("timestamp"),
                "kind": event.get("kind"),
                "scope": event.get("scope"),
                "session_id": event.get("session_id"),
                "source": event.get("source"),
            },
            "whatChanged": self._learning_change_summary(text),
            "futureTaskAffected": self._learning_future_task(text, target_name=target_name),
            "layer": layer,
            "resolver": {"kind": target_kind, "name": target_name, "primaryHome": target_page},
            "targetPage": target_page,
            "confidence": self._learning_confidence(text, layer=layer, target_kind=target_kind),
            "expiry": action_boundary["expiry"],
            "actionBoundary": action_boundary,
            "decision": decision,
        }
        return candidate

    def _learning_layer(self, text: str, *, target_kind: str) -> str:
        lower = text.lower()
        if any(marker in lower for marker in ("reminder:", "remind ", "follow up", "follow-up", "next wednesday", "next monday")) and not any(marker in lower for marker in ("decision:", "action boundary", "current", "supersedes")):
            return "open_loop"
        if any(marker in lower for marker in ("operating note", "reply", "replies", "never ", "always ", "do not ", "don't ", "should be")) and target_kind == "inbox":
            return "runtime_startup_rule"
        if target_kind in {"people", "companies", "projects", "concepts", "writing", "sources"}:
            return "gbrain_page"
        if any(marker in lower for marker in ("decision:", "promise", "current", "supersedes", "owner", "customer", "project", "company")):
            return "gbrain_page"
        if any(marker in lower for marker in ("never ", "always ", "do not ", "don't ", "should ", "must ")):
            return "runtime_startup_rule"
        return "archive"

    def _learning_resolve_target(self, text: str, event: Dict[str, Any]) -> Tuple[str, str, str]:
        checks = [
            ("projects", r"\bProject\s+([A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*){0,3})"),
            ("companies", r"\bCompany\s+([A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*){0,3})"),
            ("people", r"\b(?:Person|Developer|Customer)\s+([A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*){0,3})"),
            ("concepts", r"\bConcept\s+([A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*){0,4})"),
            ("writing", r"\bWriting\s+([A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*){0,4})"),
        ]
        for kind, pattern in checks:
            match = re.search(pattern, text)
            if match:
                name = _one_line(match.group(1), limit=80).strip(" .:-")
                return f"{kind}/{_vault_slug(name).lower()}.md", kind, name
        metadata = event.get("metadata") or {}
        title = str(metadata.get("title") or event.get("source") or "").strip()
        if title:
            return f"sources/{_vault_slug(title).lower()}.md", "sources", title
        return "inbox/needs-triage.md", "inbox", self._learning_subject(text, fallback="Needs triage")

    def _learning_subject(self, text: str, *, fallback: str = "memory") -> str:
        match = re.search(r"\b(Project|Company|Customer|Developer|Concept|Writing)\s+([A-Z][A-Za-z0-9_-]*(?:\s+[A-Z][A-Za-z0-9_-]*){0,3})", text)
        if match:
            return f"{match.group(1)} {match.group(2)}"
        return _one_line(fallback or text, limit=60)

    def _learning_change_summary(self, text: str) -> str:
        for line in str(text or "").splitlines():
            stripped = line.strip(" -\t")
            if not stripped:
                continue
            if stripped.lower().startswith(("source type:", "title:", "occurred at:", "actor:", "participants:")):
                continue
            return _one_line(stripped, limit=220)
        return _one_line(text, limit=220)

    def _learning_future_task(self, text: str, *, target_name: str) -> str:
        lower = text.lower()
        if "billing" in lower:
            return "Billing/support replies involving " + (target_name or "this object")
        if "reply" in lower or "replies" in lower:
            return "Future replies involving " + (target_name or "this object")
        if "promise" in lower:
            return "Future promise/commitment checks"
        if "decision" in lower:
            return "Future decision lookup and continuity handoff"
        if "reminder" in lower or "follow" in lower or "check" in lower:
            return "Scheduled follow-up or open-loop review"
        return "Future task not obvious; review before promotion"

    def _learning_action_boundary(self, text: str, event: Dict[str, Any]) -> Dict[str, Any]:
        lower = text.lower()
        permissions = "No special action boundary detected; review before acting."
        if "cannot promise" in lower:
            permissions = "Can draft or retrieve context, but cannot promise a fix time or outcome without owner approval."
        elif "owner approval" in lower or "ask a human" in lower:
            permissions = "Requires owner/human approval before external action."
        elif "can draft" in lower:
            permissions = "Can draft; sending/execution depends on runtime approval policy."
        expiry = "review_on_new_evidence"
        if "next wednesday" in lower:
            expiry = "after_next_wednesday_follow_up"
        elif "until" in lower:
            expiry = "explicit_until_condition_in_source"
        next_trigger = "before_related_task"
        if "next trigger:" in lower:
            next_trigger = _one_line(text.split("Next trigger:", 1)[1], limit=120) if "Next trigger:" in text else next_trigger
        elif "billing" in lower:
            next_trigger = "before_billing_related_reply"
        return {
            "scope": event.get("scope") or "private",
            "permissions": permissions,
            "expiry": expiry,
            "nextTrigger": next_trigger,
            "enforcement": "documented-boundary-only-runtime-tool-policy-enforces-actions",
        }

    def _learning_confidence(self, text: str, *, layer: str, target_kind: str) -> str:
        lower = text.lower()
        if layer == "archive":
            return "low"
        if target_kind != "inbox" and any(marker in lower for marker in ("decision:", "action boundary", "source type:")):
            return "high"
        if any(marker in lower for marker in ("maybe", "uncertain", "possibly")):
            return "low"
        return "medium"

    def _learning_reason(self, layer: str, *, target_kind: str, changes_top: bool) -> str:
        if layer == "gbrain_page":
            return f"Cross-runtime current state for {target_kind}; {'rewrite compiled truth' if changes_top else 'append timeline evidence'} before future agents depend on it."
        if layer == "runtime_startup_rule":
            return "Behavior rule likely belongs in a runtime startup/user-memory layer, not an object state page."
        if layer == "open_loop":
            return "Time-sensitive follow-up belongs in scheduler/open-loop handling, not compiled truth."
        return "No clear future reuse case; keep historical only unless an owner promotes it."

    def vault_import_preview(
        self,
        vault: str | Path,
        *,
        notes: Optional[Iterable[str | Path]] = None,
        session_id: str = "obsidian-import",
        scope: str = "private",
    ) -> Dict[str, Any]:
        vault_path = Path(vault).expanduser()
        if scope not in self.config.allowed_scopes:
            raise ValueError(f"scope '{scope}' is not allowed")
        if not vault_path.exists() or not vault_path.is_dir():
            return {"ok": False, "status": "ERROR", "error": "vault_not_found", "path": str(vault_path)}
        manifest_path = vault_path / ".total-recall-vault.json"
        manifest = self._read_json(manifest_path) if manifest_path.exists() else {}
        selected: List[Path] = []
        if notes:
            for raw in notes:
                note_path = Path(raw)
                path = note_path if note_path.is_absolute() else vault_path / note_path
                selected.append(path)
        else:
            selected = sorted(path for path in vault_path.rglob("*.md") if path.name not in {"README.md"})
        proposals = []
        for path in selected:
            try:
                resolved = path.resolve()
                resolved.relative_to(vault_path.resolve())
            except Exception:
                return {"ok": False, "status": "ERROR", "error": "unsafe_note_path", "path": str(path)}
            if not path.exists() or not path.is_file() or path.suffix.lower() != ".md":
                continue
            raw = path.read_text(encoding="utf-8")
            frontmatter, body = _split_markdown_frontmatter(raw)
            rel = path.relative_to(vault_path).as_posix()
            if frontmatter.get("type") in {"index", "guide"}:
                continue
            text = body.strip()
            if not text:
                continue
            proposal_id = f"obsimp_{_short_hash(rel + ':' + hashlib.sha256(raw.encode('utf-8')).hexdigest(), 12)}"
            proposals.append(
                {
                    "proposal_id": proposal_id,
                    "note": rel,
                    "title": _markdown_title(text) or path.stem,
                    "source_ref": frontmatter.get("source_ref"),
                    "event_hash": frontmatter.get("event_hash"),
                    "note_type": frontmatter.get("type") or "note",
                    "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                    "text": text[:8000],
                    "metadata": {
                        "schema": OBSIDIAN_IMPORT_SCHEMA_VERSION,
                        "vault": str(vault_path),
                        "note": rel,
                        "frontmatter": frontmatter,
                        "manifestStateHash": manifest.get("state_hash"),
                        "manifestLastEventHash": manifest.get("last_event_hash"),
                    },
                }
            )
        preview_id = self._new_id("obsidian_preview")
        preview = {
            "ok": True,
            "status": "PREVIEW",
            "schema": OBSIDIAN_IMPORT_SCHEMA_VERSION,
            "preview_id": preview_id,
            "created_at": utc_now(),
            "vault": str(vault_path),
            "session_id": session_id,
            "scope": scope,
            "manifest": {
                "path": str(manifest_path) if manifest_path.exists() else None,
                "stateHash": manifest.get("state_hash"),
                "lastEventHash": manifest.get("last_event_hash"),
            },
            "proposalCount": len(proposals),
            "proposals": proposals,
            "promoteHint": f"total-recall vault import-promote {preview_id}",
        }
        self._write_json(self.home / "reviews" / "obsidian" / f"{preview_id}.json", preview)
        return preview

    def vault_import_promote(
        self,
        preview_id: str,
        *,
        proposal_ids: Optional[Iterable[str]] = None,
        session_id: str = "",
        scope: str = "",
    ) -> Dict[str, Any]:
        preview_path = self.home / "reviews" / "obsidian" / f"{_safe_id(preview_id)}.json"
        if not preview_path.exists():
            return {"ok": False, "status": "ERROR", "error": "preview_not_found", "preview_id": preview_id}
        preview = self._read_json(preview_path)
        if preview.get("promoted_at"):
            return {"ok": False, "status": "ERROR", "error": "preview_already_promoted", "preview_id": preview_id}
        selected = set(str(item) for item in proposal_ids or [])
        proposals = [item for item in preview.get("proposals") or [] if not selected or str(item.get("proposal_id")) in selected]
        events = []
        for proposal in proposals:
            event = self.ingest(
                kind="obsidian_note_import",
                text=f"Obsidian note import: {proposal.get('note')}\n\n{proposal.get('text')}",
                session_id=session_id or preview.get("session_id") or "obsidian-import",
                scope=scope or preview.get("scope") or "private",
                source=f"obsidian:{proposal.get('note')}",
                metadata={
                    **(proposal.get("metadata") or {}),
                    "proposal_id": proposal.get("proposal_id"),
                    "owner_authorized": True,
                    "imported_at": utc_now(),
                },
            ).get("event")
            events.append(event)
        preview["promoted_at"] = utc_now()
        preview["promoted_event_ids"] = [event.get("event_id") for event in events if event]
        self._write_json(preview_path, preview)
        self._write_json(
            self.home / "reviews" / "obsidian" / "promoted" / f"{_safe_id(preview_id)}.json",
            {"schema": OBSIDIAN_IMPORT_SCHEMA_VERSION, "preview": preview, "events": events},
        )
        return {"ok": True, "status": "PASS", "schema": OBSIDIAN_IMPORT_SCHEMA_VERSION, "previewId": preview_id, "eventCount": len(events), "events": events}

    def federation_register(
        self,
        name: str,
        path: str | Path,
        *,
        role: str = "agent",
        scopes: Optional[Iterable[str]] = None,
        description: str = "",
    ) -> Dict[str, Any]:
        safe_name = _safe_id(name)
        home = self._resolve_total_recall_home(Path(path).expanduser())
        registry = self._federation_registry()
        target = {
            "name": safe_name,
            "path": str(home),
            "role": _safe_id(role or "agent"),
            "scopes": [str(scope) for scope in scopes or []],
            "description": description,
            "registered_at": utc_now(),
            "home_hash": sha256_json({"home": str(home.resolve())}),
        }
        registry["targets"][safe_name] = target
        self._write_json(self.home / "federation" / "targets.json", registry)
        return {"ok": True, "status": "PASS", "schema": FEDERATION_SCHEMA_VERSION, "target": target}

    def federation_list(self) -> Dict[str, Any]:
        registry = self._federation_registry()
        return {"ok": True, "status": "PASS", "schema": FEDERATION_SCHEMA_VERSION, "targets": list(registry.get("targets", {}).values())}

    def federation_remove(self, name: str) -> Dict[str, Any]:
        registry = self._federation_registry()
        removed = registry.get("targets", {}).pop(_safe_id(name), None)
        self._write_json(self.home / "federation" / "targets.json", registry)
        return {"ok": removed is not None, "status": "PASS" if removed else "MISSING", "removed": removed}

    def federation_query(
        self,
        query: str,
        *,
        targets: Optional[Iterable[str]] = None,
        authorize: bool = False,
        mode: str = "normal",
        allowed_scopes: Optional[Iterable[str]] = None,
        max_results: int = 8,
        at_time: str = "",
    ) -> Dict[str, Any]:
        registry = self._federation_registry()
        target_names = [str(item) for item in targets or registry.get("targets", {}).keys()]
        homes = []
        resolved_targets = []
        for name in target_names:
            target = registry.get("targets", {}).get(_safe_id(name))
            if not target:
                continue
            resolved_targets.append(target)
            homes.append(target.get("path"))
        result = self.knowledge_query(
            query,
            mode=mode,
            max_results=max_results,
            at_time=at_time,
            allowed_scopes=allowed_scopes,
            federate=homes,
            federation_authorized=authorize,
        )
        result["registry"] = {"targets": resolved_targets, "authorized": authorize}
        return result

    def export_obsidian_vault(
        self,
        out: str | Path,
        *,
        force: bool = False,
        allowed_scopes: Optional[Iterable[str]] = None,
        max_events: int = 500,
        max_entities: int = 100,
    ) -> Dict[str, Any]:
        out_path = Path(out).expanduser()
        try:
            resolved_out = out_path.resolve(strict=False)
            if resolved_out == self.home or self.home in resolved_out.parents:
                return {
                    "ok": False,
                    "status": "ERROR",
                    "error": "vault_output_inside_total_recall_home",
                    "path": str(out_path),
                    "nextSteps": ["Choose an output folder outside the Total Recall home/store."],
                }
        except Exception:
            pass
        scopes = list(allowed_scopes or self.config.allowed_scopes)
        state = self.reduce_state(write=True)
        events = [event for event in self._read_events(verify_chain=True) if str(event.get("scope") or "private") in scopes]
        events = events[-max(1, int(max_events or 500)) :]
        if out_path.exists():
            if not out_path.is_dir():
                return {"ok": False, "status": "ERROR", "error": "output_path_is_not_directory", "path": str(out_path)}
            if any(out_path.iterdir()) and not force:
                return {
                    "ok": False,
                    "status": "EXISTS",
                    "error": "vault_output_not_empty",
                    "path": str(out_path),
                    "nextSteps": ["Re-run with --force to regenerate the derived vault."],
                }
            if force:
                shutil.rmtree(out_path)
        out_path.mkdir(parents=True, exist_ok=True)

        graph = self.knowledge_graph_inspect(limit=max_entities, allowed_scopes=scopes)
        entities = graph.get("entities") or []
        edges = graph.get("edges") or []
        sources_by_ref = {f"ledger:{event.get('event_id')}": event for event in events}
        entities_by_id = {str(item.get("entity_id")): item for item in entities if item.get("entity_id")}
        entity_pages = {
            str(item.get("entity_id")): f"Entities/{_vault_slug(str(item.get('name') or item.get('entity_id')))}-{_short_hash(str(item.get('entity_id')))}"
            for item in entities
            if item.get("entity_id")
        }
        source_pages = {ref: f"Sources/{_vault_slug(ref.replace(':', '_'))}" for ref in sources_by_ref}
        document_events = [event for event in events if str(event.get("kind") or "") == "document"]
        documents = _group_document_events(document_events)
        document_pages = {doc_path: f"Documents/{_vault_slug(doc_path)}-{_short_hash(doc_path)}" for doc_path in documents}
        written: List[str] = []

        def write_page(rel_no_ext: str, content: str) -> None:
            path = out_path / f"{rel_no_ext}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(str(path))

        write_page(
            "Index",
            self._obsidian_index_markdown(
                state=state,
                events=events,
                entities=entities,
                edges=edges,
                documents=documents,
                entity_pages=entity_pages,
                document_pages=document_pages,
            ),
        )
        write_page("Graph Legend", self._obsidian_graph_legend_markdown())
        write_page("Compiled Truth", self._obsidian_compiled_truth_markdown())

        for doc_path, doc_events in sorted(documents.items()):
            write_page(document_pages[doc_path], self._obsidian_document_markdown(doc_path, doc_events, source_pages=source_pages))

        for event in events:
            ref = f"ledger:{event.get('event_id')}"
            source_entities = [entity for entity in entities if entity.get("source_ref") == ref]
            source_edges = [edge for edge in edges if edge.get("source_ref") == ref]
            write_page(
                source_pages[ref],
                self._obsidian_source_markdown(
                    event,
                    entities=source_entities,
                    edges=source_edges,
                    entities_by_id=entities_by_id,
                    entity_pages=entity_pages,
                    document_pages=document_pages,
                ),
            )

        for entity in entities:
            entity_id = str(entity.get("entity_id") or "")
            if not entity_id:
                continue
            related_edges = [edge for edge in edges if edge.get("source_entity_id") == entity_id or edge.get("target_entity_id") == entity_id]
            write_page(
                entity_pages[entity_id],
                self._obsidian_entity_markdown(
                    entity,
                    edges=related_edges,
                    entities_by_id=entities_by_id,
                    entity_pages=entity_pages,
                    source_pages=source_pages,
                ),
            )

        categorized = {
            "Decisions": [event for event in events if _event_looks_decision(event)],
            "Promises": [event for event in events if _event_looks_promise(event)],
            "Tasks": [event for event in events if _event_looks_task(event)],
        }
        for folder, folder_events in categorized.items():
            for event in folder_events:
                write_page(f"{folder}/{_vault_slug(str(event.get('event_id') or folder))}", self._obsidian_category_markdown(folder[:-1], event, source_pages=source_pages))

        for day, day_events in sorted(_group_events_by_day(events).items()):
            write_page(f"Timeline/{day}", self._obsidian_timeline_markdown(day, day_events, source_pages=source_pages))

        write_page(
            "README",
            "# Total Recall Vault Export\n\n"
            "This Obsidian vault is a derived projection. The Total Recall ledger, checkpoints, and signed anchors remain the authority.\n\n"
            "Regenerate this vault from Total Recall when memory changes. Edited notes become canonical only through `total-recall vault import-preview` followed by `total-recall vault import-promote`.\n",
        )
        manifest = {
            "schema": OBSIDIAN_VAULT_SCHEMA_VERSION,
            "exported_at": utc_now(),
            "total_recall_home": str(self.home),
            "state_hash": state.get("state_hash"),
            "last_event_hash": state.get("last_event_hash"),
            "event_count": len(events),
            "document_count": len(documents),
            "entity_count": len(entities),
            "edge_count": len(edges),
            "scopes": scopes,
            "files": [str(Path(path).relative_to(out_path)) for path in written],
            "authority": "ledger/checkpoints/anchors",
            "import_status": "selected_edit_preview_and_promote_available",
        }
        self._write_json(out_path / ".total-recall-vault.json", manifest)
        return {
            "ok": True,
            "status": "PASS",
            "schema": OBSIDIAN_VAULT_SCHEMA_VERSION,
            "path": str(out_path),
            "manifest": str(out_path / ".total-recall-vault.json"),
            "files": written,
            "fileCount": len(written) + 1,
            "eventCount": len(events),
            "documentCount": len(documents),
            "entityCount": len(entities),
            "edgeCount": len(edges),
            "authority": "ledger/checkpoints/anchors",
            "note": "Vault is a derived Obsidian projection; Total Recall remains authority.",
        }

    def _obsidian_index_markdown(
        self,
        *,
        state: Dict[str, Any],
        events: List[Dict[str, Any]],
        entities: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        documents: Dict[str, List[Dict[str, Any]]],
        entity_pages: Dict[str, str],
        document_pages: Dict[str, str],
    ) -> str:
        lines = [
            _vault_frontmatter(
                {
                    "type": "index",
                    "schema": OBSIDIAN_VAULT_SCHEMA_VERSION,
                    "generated_at": utc_now(),
                    "total_recall_home": str(self.home),
                    "state_hash": state.get("state_hash"),
                    "last_event_hash": state.get("last_event_hash"),
                    "event_count": len(events),
                    "entity_count": len(entities),
                    "edge_count": len(edges),
                    "document_count": len(documents),
                }
            ),
            "# Total Recall Vault",
            "",
            "This vault is a derived Obsidian projection of Total Recall memory. The signed ledger, checkpoints, and anchors remain the authority.",
            "",
            "## Start Here",
            f"- {_vault_link('Compiled Truth')}",
            f"- {_vault_link('Graph Legend')}",
            "- `Sources/` holds one cited ledger page per memory event.",
            "- `Entities/` turns the derived knowledge graph into wikilinked pages.",
            "- `Documents/` groups document-ingest chunks into readable source files.",
            "- `Timeline/` groups memory by day.",
            "",
            "## Counts",
            f"- Events exported: `{len(events)}`",
            f"- Documents: `{len(documents)}`",
            f"- Entities: `{len(entities)}`",
            f"- Edges: `{len(edges)}`",
            "",
        ]
        if documents:
            lines.extend(["## Documents"])
            for doc_path in sorted(documents)[:40]:
                lines.append(f"- {_vault_link(document_pages[doc_path], doc_path)}")
            if len(documents) > 40:
                lines.append(f"- ... {len(documents) - 40} more")
            lines.append("")
        if entities:
            lines.extend(["## Entities"])
            for entity in entities[:40]:
                entity_id = str(entity.get("entity_id") or "")
                page = entity_pages.get(entity_id)
                if page:
                    label = str(entity.get("name") or entity_id)
                    lines.append(f"- {_vault_link(page, label)} `{entity.get('type') or 'entity'}`")
            if len(entities) > 40:
                lines.append(f"- ... {len(entities) - 40} more")
            lines.append("")
        if events:
            lines.extend(["## Recent Memory"])
            for event in list(reversed(events))[:25]:
                ref = _event_source_ref(event)
                source_page = f"Sources/{_vault_slug(ref.replace(':', '_'))}"
                lines.append(
                    f"- `{event.get('timestamp')}` {_vault_link(source_page, _event_title(event))} "
                    f"`{event.get('kind')}` `{event.get('scope')}`"
                )
            if len(events) > 25:
                lines.append(f"- ... {len(events) - 25} more")
            lines.append("")
        lines.extend(
            [
                "## Edit Boundary",
                "Edit these notes for reading and annotation. Changes become Total Recall memory only through `vault import-preview` and explicit owner promotion.",
                "",
            ]
        )
        return "\n".join(lines)

    def _obsidian_graph_legend_markdown(self) -> str:
        return "\n".join(
            [
                _vault_frontmatter({"type": "guide", "schema": OBSIDIAN_VAULT_SCHEMA_VERSION, "generated_at": utc_now()}),
                "# Graph Legend",
                "",
                "Obsidian's graph view is powered by wikilinks generated from Total Recall's derived knowledge graph.",
                "",
                "## Node Types",
                "- `Index.md`: the vault home.",
                "- `Compiled Truth.md`: current derived truth projection with ledger citations.",
                "- `Sources/`: authoritative evidence pages for each exported ledger event.",
                "- `Entities/`: graph entities extracted from cited source events.",
                "- `Documents/`: grouped document-ingest chunks.",
                "- `Decisions/`, `Promises/`, `Tasks/`: convenience views over ledger events.",
                "- `Timeline/`: day-by-day event pages.",
                "",
                "## Authority",
                "The vault is disposable and regenerable. Total Recall's ledger, checkpoints, and anchors remain canonical.",
                "",
                "## Future Import",
                "Two-way import is explicit: select edited notes, preview proposed ledger events, then promote them with owner approval.",
                "",
            ]
        )

    def _obsidian_compiled_truth_markdown(self) -> str:
        prefix = _vault_frontmatter(
            {"type": "compiled_truth", "schema": OBSIDIAN_VAULT_SCHEMA_VERSION, "generated_at": utc_now()}
        )
        try:
            payload = self.knowledge_compiled_truth_show(format_="md")
            body = str(payload.get("text") or "") if payload.get("ok") else ""
        except Exception as exc:
            body = f"# Total Recall Compiled Truth\n\nCompiled truth could not be generated during vault export: `{exc}`.\n"
        if not body.strip():
            body = "# Total Recall Compiled Truth\n\nNo compiled truth projection is available yet.\n"
        return (
            prefix
            + body.strip()
            + "\n\n## Vault Note\n\nThis page is regenerated from the local knowledge projection. Ledger citations remain the source of truth.\n"
        )

    def _obsidian_document_markdown(
        self,
        doc_path: str,
        doc_events: List[Dict[str, Any]],
        *,
        source_pages: Dict[str, str],
    ) -> str:
        source_refs = [_event_source_ref(event) for event in doc_events]
        lines = [
            _vault_frontmatter(
                {
                    "type": "document",
                    "schema": OBSIDIAN_VAULT_SCHEMA_VERSION,
                    "document_path": doc_path,
                    "chunk_count": len(doc_events),
                    "source_refs": source_refs,
                    "generated_at": utc_now(),
                }
            ),
            f"# {doc_path}",
            "",
            "This note groups document-ingest chunks from the Total Recall ledger.",
            "",
            "## Source Chunks",
        ]
        for event in doc_events:
            ref = _event_source_ref(event)
            metadata = event.get("metadata") or {}
            chunk = metadata.get("chunk_index") or "?"
            total = metadata.get("chunk_count") or "?"
            source_link = _vault_link(source_pages.get(ref, ""), f"chunk {chunk}/{total}") if source_pages.get(ref) else f"`{ref}`"
            lines.append(f"- {source_link} `{ref}` evidence `{event.get('hash')}`")
        lines.append("")
        lines.append("## Extracts")
        for event in doc_events[:20]:
            ref = _event_source_ref(event)
            metadata = event.get("metadata") or {}
            chunk = metadata.get("chunk_index") or "?"
            total = metadata.get("chunk_count") or "?"
            lines.extend(
                [
                    "",
                    f"### Chunk {chunk}/{total}",
                    _vault_link(source_pages.get(ref, ""), ref) if source_pages.get(ref) else f"`{ref}`",
                    "",
                    _fenced_text(str(event.get("text") or "")[:1600]),
                ]
            )
        if len(doc_events) > 20:
            lines.append(f"\nAdditional chunks omitted from this document note: `{len(doc_events) - 20}`.")
        lines.append("")
        return "\n".join(lines)

    def _obsidian_source_markdown(
        self,
        event: Dict[str, Any],
        *,
        entities: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        entities_by_id: Dict[str, Dict[str, Any]],
        entity_pages: Dict[str, str],
        document_pages: Dict[str, str],
    ) -> str:
        metadata = event.get("metadata") or {}
        ref = _event_source_ref(event)
        doc_path = str(metadata.get("document_path") or "")
        lines = [
            _vault_frontmatter(
                {
                    "type": "source",
                    "schema": OBSIDIAN_VAULT_SCHEMA_VERSION,
                    "source_ref": ref,
                    "event_id": event.get("event_id"),
                    "event_hash": event.get("hash"),
                    "kind": event.get("kind"),
                    "scope": event.get("scope"),
                    "session_id": event.get("session_id"),
                    "timestamp": event.get("timestamp"),
                    "document_path": doc_path or None,
                    "generated_at": utc_now(),
                }
            ),
            f"# {_event_title(event)}",
            "",
            f"- Source ref: `{ref}`",
            f"- Evidence hash: `{event.get('hash')}`",
            f"- Kind: `{event.get('kind')}`",
            f"- Scope: `{event.get('scope')}`",
            f"- Session: `{event.get('session_id')}`",
            f"- Timestamp: `{event.get('timestamp')}`",
            "",
        ]
        if doc_path and document_pages.get(doc_path):
            lines.extend(["## Document", f"- {_vault_link(document_pages[doc_path], doc_path)}", ""])
        if entities:
            lines.extend(["## Entities"])
            for entity in entities:
                entity_id = str(entity.get("entity_id") or "")
                page = entity_pages.get(entity_id)
                label = str(entity.get("name") or entity_id)
                lines.append(f"- {_vault_link(page, label) if page else label} `{entity.get('type')}` confidence `{entity.get('confidence')}`")
            lines.append("")
        if edges:
            lines.extend(["## Relationships"])
            for edge in edges:
                source = entities_by_id.get(str(edge.get("source_entity_id") or ""), {})
                target = entities_by_id.get(str(edge.get("target_entity_id") or ""), {})
                source_page = entity_pages.get(str(edge.get("source_entity_id") or ""))
                target_page = entity_pages.get(str(edge.get("target_entity_id") or ""))
                source_label = str(source.get("name") or edge.get("source_entity_id") or "source")
                target_label = str(target.get("name") or edge.get("target_entity_id") or "target")
                source_link = _vault_link(source_page, source_label) if source_page else source_label
                target_link = _vault_link(target_page, target_label) if target_page else target_label
                lines.append(f"- {source_link} -- `{edge.get('relation')}` --> {target_link} evidence `{edge.get('evidence_hash')}`")
            lines.append("")
        lines.extend(["## Text", "", _fenced_text(str(event.get("text") or "")), ""])
        return "\n".join(lines)

    def _obsidian_entity_markdown(
        self,
        entity: Dict[str, Any],
        *,
        edges: List[Dict[str, Any]],
        entities_by_id: Dict[str, Dict[str, Any]],
        entity_pages: Dict[str, str],
        source_pages: Dict[str, str],
    ) -> str:
        entity_id = str(entity.get("entity_id") or "")
        source_ref = str(entity.get("source_ref") or "")
        source_link = _vault_link(source_pages[source_ref], source_ref) if source_ref in source_pages else f"`{source_ref}`"
        lines = [
            _vault_frontmatter(
                {
                    "type": "entity",
                    "schema": OBSIDIAN_VAULT_SCHEMA_VERSION,
                    "entity_id": entity_id,
                    "name": entity.get("name"),
                    "entity_type": entity.get("type"),
                    "source_ref": source_ref,
                    "evidence_hash": entity.get("evidence_hash"),
                    "confidence": entity.get("confidence"),
                    "scope": entity.get("scope"),
                    "created_at": entity.get("created_at"),
                    "generated_at": utc_now(),
                }
            ),
            f"# {entity.get('name') or entity_id}",
            "",
            f"- Type: `{entity.get('type')}`",
            f"- Confidence: `{entity.get('confidence')}`",
            f"- Evidence: {source_link}",
            f"- Evidence hash: `{entity.get('evidence_hash')}`",
            "",
            "## Relationships",
        ]
        if not edges:
            lines.append("- No exported relationships.")
        for edge in edges:
            source_id = str(edge.get("source_entity_id") or "")
            target_id = str(edge.get("target_entity_id") or "")
            other_id = target_id if source_id == entity_id else source_id
            other = entities_by_id.get(other_id, {})
            other_page = entity_pages.get(other_id)
            other_label = str(other.get("name") or other_id or "unknown")
            direction = "to" if source_id == entity_id else "from"
            rel_source = str(edge.get("source_ref") or "")
            rel_source_link = _vault_link(source_pages[rel_source], rel_source) if rel_source in source_pages else f"`{rel_source}`"
            lines.append(
                f"- `{edge.get('relation')}` {direction} {_vault_link(other_page, other_label) if other_page else other_label} "
                f"via {rel_source_link} evidence `{edge.get('evidence_hash')}`"
            )
        lines.append("")
        return "\n".join(lines)

    def _obsidian_category_markdown(
        self,
        kind: str,
        event: Dict[str, Any],
        *,
        source_pages: Dict[str, str],
    ) -> str:
        ref = _event_source_ref(event)
        source_link = _vault_link(source_pages[ref], ref) if ref in source_pages else f"`{ref}`"
        lines = [
            _vault_frontmatter(
                {
                    "type": kind.lower(),
                    "schema": OBSIDIAN_VAULT_SCHEMA_VERSION,
                    "source_ref": ref,
                    "event_id": event.get("event_id"),
                    "event_hash": event.get("hash"),
                    "scope": event.get("scope"),
                    "session_id": event.get("session_id"),
                    "timestamp": event.get("timestamp"),
                    "generated_at": utc_now(),
                }
            ),
            f"# {kind}: {_event_title(event)}",
            "",
            f"- Evidence: {source_link}",
            f"- Evidence hash: `{event.get('hash')}`",
            "",
            "## Text",
            "",
            _fenced_text(str(event.get("text") or "")),
            "",
        ]
        return "\n".join(lines)

    def _obsidian_timeline_markdown(
        self,
        day: str,
        events: List[Dict[str, Any]],
        *,
        source_pages: Dict[str, str],
    ) -> str:
        lines = [
            _vault_frontmatter(
                {
                    "type": "timeline",
                    "schema": OBSIDIAN_VAULT_SCHEMA_VERSION,
                    "day": day,
                    "event_count": len(events),
                    "generated_at": utc_now(),
                }
            ),
            f"# {day}",
            "",
        ]
        for event in sorted(events, key=lambda item: str(item.get("timestamp") or "")):
            ref = _event_source_ref(event)
            source_link = _vault_link(source_pages[ref], _event_title(event)) if ref in source_pages else f"`{ref}`"
            tags = []
            if _event_looks_decision(event):
                tags.append("decision")
            if _event_looks_promise(event):
                tags.append("promise")
            if _event_looks_task(event):
                tags.append("task")
            tag_text = f" {' '.join('#' + tag for tag in tags)}" if tags else ""
            lines.append(
                f"- `{event.get('timestamp')}` {source_link} `{event.get('kind')}` `{event.get('scope')}`{tag_text}"
            )
        lines.append("")
        return "\n".join(lines)

    def _document_candidates(self, paths: Sequence[str | Path], *, recursive: bool) -> List[Tuple[Path, Path, bool]]:
        candidates: List[Tuple[Path, Path, bool]] = []
        seen = set()
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.exists():
                candidates.append((path, path.parent, True))
                continue
            if path.is_file():
                resolved = path.resolve()
                if resolved not in seen:
                    candidates.append((path, path.parent, True))
                    seen.add(resolved)
                continue
            if not path.is_dir():
                candidates.append((path, path.parent, True))
                continue
            iterator = path.rglob("*") if recursive else path.iterdir()
            for item in sorted(iterator):
                if not item.is_file():
                    continue
                resolved = item.resolve()
                if resolved in seen:
                    continue
                candidates.append((item, path, False))
                seen.add(resolved)
        return candidates

    def _prepare_document_file(
        self,
        path: Path,
        *,
        root: Path,
        explicit: bool,
        include_extensions: set[str],
        exclude_globs: set[str],
        max_file_bytes: int,
        chunk_chars: int,
        ingest_id: str,
        session_id: str,
        scope: str,
    ) -> Dict[str, Any]:
        display_path = _display_document_path(path, root=root)
        file_info: Dict[str, Any] = {
            "path": str(path),
            "documentPath": display_path,
            "status": "skipped",
            "chunks": 0,
        }
        if not path.exists():
            file_info["reason"] = "missing"
            return {"file": file_info, "events": []}
        if not path.is_file():
            file_info["reason"] = "not_a_file"
            return {"file": file_info, "events": []}
        if not explicit and _is_ignored_document_path(path, root=root, exclude_globs=exclude_globs):
            file_info["reason"] = "ignored_path"
            return {"file": file_info, "events": []}
        suffix = path.suffix.lower()
        if suffix not in include_extensions:
            file_info["reason"] = "unsupported_extension"
            file_info["extension"] = suffix
            return {"file": file_info, "events": []}
        stat = path.stat()
        file_info["bytes"] = stat.st_size
        file_info["extension"] = suffix
        if stat.st_size > max(1, int(max_file_bytes)):
            file_info["reason"] = "file_too_large"
            file_info["maxFileBytes"] = max_file_bytes
            return {"file": file_info, "events": []}
        raw = path.read_bytes()
        if _looks_binary(raw):
            file_info["reason"] = "binary_or_unsupported_text"
            return {"file": file_info, "events": []}
        text = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            file_info["reason"] = "empty"
            return {"file": file_info, "events": []}
        file_hash = hashlib.sha256(raw).hexdigest()
        chunks = _chunk_document_text(text, max_chars=chunk_chars)
        file_info.update(
            {
                "status": "planned",
                "reason": "",
                "chunks": len(chunks),
                "sha256": file_hash,
            }
        )
        events = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_text = f"Document: {display_path}\nChunk: {index}/{len(chunks)}\n\n{chunk}".strip()
            events.append(
                {
                    "event_id": self._new_id("evt"),
                    "timestamp": utc_now(),
                    "kind": "document",
                    "session_id": session_id or "documents",
                    "scope": scope,
                    "source": f"document:{display_path}",
                    "text": chunk_text,
                    "metadata": {
                        "schema": DOCUMENT_INGEST_SCHEMA_VERSION,
                        "document_ingest_id": ingest_id,
                        "document_path": display_path,
                        "file_name": path.name,
                        "file_extension": suffix,
                        "file_sha256": file_hash,
                        "file_size_bytes": stat.st_size,
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                        "chunk_chars": len(chunk),
                    },
                }
            )
        return {"file": file_info, "events": events}

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
        state = self._state_from_events(events)
        if write:
            self._write_json(self.state_file, state)
        return state

    def _state_from_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
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

        state: Dict[str, Any] = {}
        current_state: Dict[str, Any] = {}
        try:
            events = self._read_events(verify_chain=True)
            current_state = self._state_from_events(events)
            checkpoint_event_count = int(checkpoint.get("event_count") or -1)
            details["stateHash"] = current_state.get("state_hash")
            details["currentStateHash"] = current_state.get("state_hash")
            details["currentEventCount"] = current_state.get("event_count")
            details["currentLastEventHash"] = current_state.get("last_event_hash")
            details["checkpointEventCount"] = checkpoint_event_count
            if checkpoint_event_count < 0 or checkpoint_event_count > len(events):
                failures.append("checkpoint_event_count_invalid")
                state = current_state
            else:
                state = self._state_from_events(events[:checkpoint_event_count])
            details["checkpointStateHash"] = state.get("state_hash")
            if current_state.get("event_count") != checkpoint_event_count:
                details.setdefault("warnings", []).append("checkpoint_stale")
                details["checkpointLagEvents"] = current_state.get("event_count", 0) - checkpoint_event_count
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
                details["indexRebuild"] = self._rebuild_index_locked(state=current_state or state)
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

        # Reports are generated audit artifacts. They can contain prior search
        # payloads and rehydrate output, so indexing them lets Total Recall
        # recursively recall its own recall transcripts.
        for directory, kind in (
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
            "reviews",
            "federation",
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
                for rel_dir in ("ledger", "state", "checkpoints", "anchors", "reports", "incidents", "external-memory", "keys", "index", "reviews", "federation"):
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

        required_dirs = ["ledger", "state", "checkpoints", "anchors", "reports", "incidents", "external-memory", "index", "keys", "knowledge", "reviews", "federation"]
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
            knowledge_status = self.knowledge_status()
            add("knowledge_engine_status", bool(knowledge_status.get("ok")), status=knowledge_status.get("status"), index=knowledge_status.get("index"), graph=knowledge_status.get("graph"))
        except Exception as exc:
            add("knowledge_engine_status", False, error=str(exc))

        try:
            public_key = self._ed25519_public_key_hex()
            add("ed25519_public_key", bool(public_key), keyId=self._key_id())
        except Exception as exc:
            add("ed25519_public_key", False, error=str(exc))

        advisory_checks = {"checkpoint_present", "knowledge_engine_status"}
        ok = all(check.get("ok") for check in checks if check["name"] not in advisory_checks) and latest_checkpoint is not None
        payload = {"ok": ok, "status": "PASS" if ok else "DEGRADED", "home": str(self.home), "checks": checks}
        payload["report"] = self._write_report("doctor", "latest", payload)
        return payload

    def trust_gate_status(self) -> Dict[str, Any]:
        latest = self.home / "reports" / "trust_gate_latest.json"
        if not latest.exists():
            return {"ok": False, "status": "NO_TRUST_GATE", "error": "trust_gate_report_not_found", "reportFile": str(latest)}
        payload = self._read_json(latest)
        return {"ok": bool(payload.get("ok")), "status": payload.get("status") or "UNKNOWN", "reportFile": str(latest), **payload}

    def trust_gate_run(self, *, persist: bool = True) -> Dict[str, Any]:
        self._ensure_layout()
        gate_id = f"gate_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
        checks: List[Dict[str, Any]] = []
        state: Dict[str, Any] = {}

        def add(name: str, ok: bool, summary: str, *, evidence: Optional[Dict[str, Any]] = None, severity: str = "required") -> None:
            checks.append(self._trust_gate_check(name, ok, summary, evidence=evidence, severity=severity))

        try:
            state = self.reduce_state(write=True)
            add(
                "real_store_ledger_hash_chain",
                True,
                "Ledger reduces with hash-chain verification enabled.",
                evidence={
                    "eventCount": state.get("event_count"),
                    "stateHash": state.get("state_hash"),
                    "lastEventHash": state.get("last_event_hash"),
                },
            )
        except Exception as exc:
            add("real_store_ledger_hash_chain", False, f"Ledger hash-chain verification failed: {exc}", evidence={"error": str(exc)})
            state = {"event_count": -1}

        event_count = int(state.get("event_count") or 0)
        latest_checkpoint = self._latest_file(self.home / "checkpoints", "*.json")
        if event_count <= 0 and latest_checkpoint is None:
            add(
                "real_store_checkpoint_anchor_current",
                True,
                "Empty store has no ledger events requiring a checkpoint yet.",
                evidence={"eventCount": event_count, "checkpointFile": None},
            )
        elif latest_checkpoint is None:
            add(
                "real_store_checkpoint_anchor_current",
                False,
                "Ledger has events but no checkpoint/anchor to pin durable state.",
                evidence={"eventCount": event_count, "checkpointFile": None},
            )
        else:
            verification = self.verify(checkpoint_file=str(latest_checkpoint))
            lag = int(verification.get("checkpointLagEvents") or 0)
            add(
                "real_store_checkpoint_anchor_current",
                bool(verification.get("ok")) and lag == 0,
                "Latest checkpoint and signed anchor verify and pin the current ledger state.",
                evidence={
                    "verificationStatus": verification.get("status"),
                    "failures": verification.get("failures") or [],
                    "warnings": verification.get("warnings") or [],
                    "checkpointLagEvents": lag,
                    "checkpointFile": str(latest_checkpoint),
                },
            )

        try:
            index_status = self.index_status(state=state if state.get("event_count", -1) >= 0 else None)
            if not index_status.get("fresh"):
                rebuilt = self.rebuild_index(backends=["sqlite-fts"])
                index_status = rebuilt.get("index") or self.index_status()
            add(
                "real_store_core_index_rebuildable",
                bool(index_status.get("fresh")),
                "Core retrieval index is fresh or was rebuilt from the ledger.",
                evidence={
                    "fresh": index_status.get("fresh"),
                    "backends": index_status.get("backends"),
                    "eventCount": index_status.get("eventCount"),
                },
            )
        except Exception as exc:
            add("real_store_core_index_rebuildable", False, f"Core retrieval index check failed: {exc}", evidence={"error": str(exc)})

        try:
            knowledge_status = self.knowledge_status()
            knowledge_index = knowledge_status.get("index") or {}
            if not knowledge_index.get("fresh"):
                self.knowledge_index_rebuild()
                knowledge_status = self.knowledge_status()
                knowledge_index = knowledge_status.get("index") or {}
            graph = knowledge_status.get("graph") or {}
            add(
                "real_store_knowledge_authority",
                bool(knowledge_status.get("ok")) and bool(knowledge_index.get("fresh")) and graph.get("uncitedActiveItems", 0) == 0,
                "Knowledge Engine derives from the current ledger and graph evidence remains cited.",
                evidence={
                    "status": knowledge_status.get("status"),
                    "indexFresh": knowledge_index.get("fresh"),
                    "sourceCount": knowledge_index.get("sourceCount"),
                    "graphStatus": graph.get("status"),
                    "uncitedActiveItems": graph.get("uncitedActiveItems"),
                },
            )
        except Exception as exc:
            add("real_store_knowledge_authority", False, f"Knowledge Engine authority check failed: {exc}", evidence={"error": str(exc)})

        checks.extend(self._trust_gate_fixture_checks())
        checks.append(self._trust_gate_export_import_check(state))
        checks.extend(self._trust_gate_hermes_bundle_checks())

        failed_required = [check for check in checks if check.get("severity") == "required" and not check.get("ok")]
        failed_advisory = [check for check in checks if check.get("severity") != "required" and not check.get("ok")]
        ok = not failed_required
        payload = {
            "ok": ok,
            "status": "PASS" if ok else "FAIL_CLOSED",
            "schema": TRUST_GATE_SCHEMA_VERSION,
            "gate_id": gate_id,
            "created_at": utc_now(),
            "home": str(self.home),
            "authority": "hard-coded-runtime-checks-not-docs-or-agent-vibes",
            "summary": {
                "totalChecks": len(checks),
                "passed": sum(1 for check in checks if check.get("ok")),
                "failedRequired": len(failed_required),
                "failedAdvisory": len(failed_advisory),
            },
            "failedRequired": [check["name"] for check in failed_required],
            "failedAdvisory": [check["name"] for check in failed_advisory],
            "releaseGate": {
                "publicFacingPlugin": ok,
                "minimumRequiredChecks": len([check for check in checks if check.get("severity") == "required"]),
                "failedRequired": [check["name"] for check in failed_required],
            },
            "checks": checks,
        }
        if persist:
            payload["report"] = self._write_report("trust_gate", gate_id, payload)
            self._write_json(self.home / "reports" / "trust_gate_latest.json", payload)
            if not ok:
                incident = self.create_incident(
                    title="Trust gate failed",
                    severity="FAIL_CLOSED",
                    summary=", ".join(payload["failedRequired"]),
                    metadata={
                        "gate_id": gate_id,
                        "failedRequired": payload["failedRequired"],
                        "report": payload.get("report"),
                        "created_at": payload.get("created_at"),
                    },
                )
                payload["incident"] = incident.get("incident")
        return payload

    def _trust_gate_fixture_checks(self) -> List[Dict[str, Any]]:
        checks: List[Dict[str, Any]] = []

        def add(name: str, ok: bool, summary: str, *, evidence: Optional[Dict[str, Any]] = None, severity: str = "required") -> None:
            checks.append(self._trust_gate_check(name, ok, summary, evidence=evidence, severity=severity))

        try:
            with tempfile.TemporaryDirectory(prefix="total-recall-trust-gate-") as tmp:
                root = Path(tmp)
                home = root / "store"
                fed_home = root / "federated"
                imported_home = root / "imported"
                core = TotalRecallCore(TotalRecallConfig(home=home, enable_lancedb=False, enable_qmd=False))

                first = core.ingest_source(
                    source_type="meeting",
                    title="January Promise Review",
                    text="Decision: Brand promise is same-day delivery.",
                    occurred_at="2026-01-10T10:00:00Z",
                    scope="public",
                    session_id="trust-gate",
                    metadata={"freshness_category": "promise"},
                )
                second = core.ingest_source(
                    source_type="slack",
                    title="February Promise Update",
                    text="Decision: Brand promise is seven-day fulfillment. This supersedes old same-day promise.",
                    occurred_at="2026-02-10T10:00:00Z",
                    scope="public",
                    session_id="trust-gate",
                    metadata={"freshness_category": "promise"},
                )
                events = core._read_events(verify_chain=True)
                source_events = [event for event in events if str(event.get("kind") or "").startswith("source_")]
                add(
                    "fixture_source_ingest_ledgered",
                    bool(first.get("ok")) and bool(second.get("ok")) and len(source_events) == 2 and source_events[0].get("hash") and source_events[1].get("prev_hash") == source_events[0].get("hash"),
                    "Working-context source ingest writes hash-chained ledger events with effective timestamps.",
                    evidence={
                        "sourceKinds": [event.get("kind") for event in source_events],
                        "occurredAt": [(event.get("metadata") or {}).get("occurred_at") for event in source_events],
                    },
                )

                core.knowledge_index_rebuild()
                freshness = core.knowledge_freshness_report(
                    entity="brand promise",
                    category="promise",
                    at_time="2026-03-01T00:00:00Z",
                    allowed_scopes=["public"],
                )
                counts = freshness.get("counts") or {}
                add(
                    "fixture_freshness_supersession",
                    bool(freshness.get("ok")) and counts.get("current") == 1 and counts.get("superseded") == 1,
                    "Freshness reporting distinguishes current from superseded promises.",
                    evidence={"counts": counts, "itemCount": len(freshness.get("items") or [])},
                )

                timeline = core.knowledge_graph_timeline(
                    "brand promise",
                    at_time="2026-01-20T00:00:00Z",
                    allowed_scopes=["public"],
                )
                as_of_text = canonical_json(timeline.get("asOf") or [])
                after_text = canonical_json(timeline.get("afterAsOf") or [])
                add(
                    "fixture_temporal_graph_timeline",
                    bool(timeline.get("ok")) and "same-day delivery" in as_of_text and "seven-day fulfillment" in after_text,
                    "Temporal graph timeline separates as-of evidence from later changes.",
                    evidence={"asOfCount": len(timeline.get("asOf") or []), "afterAsOfCount": len(timeline.get("afterAsOf") or [])},
                )

                vault = root / "vault"
                exported_vault = core.export_obsidian_vault(vault, allowed_scopes=["public"], force=True)
                edited = vault / "Edited Promise.md"
                edited.write_text(
                    "---\ntype: \"edited_note\"\n---\n# Edited Promise\n\nDecision: Storefront promise is seven-day fulfillment after owner review.\n",
                    encoding="utf-8",
                )
                before_preview_count = core.health()["eventCount"]
                preview = core.vault_import_preview(vault, notes=["Edited Promise.md"], session_id="trust-gate-import", scope="internal")
                after_preview_count = core.health()["eventCount"]
                preview_path = core.home / "reviews" / "obsidian" / f"{preview.get('preview_id')}.json"
                add(
                    "fixture_obsidian_preview_no_ledger_write",
                    bool(exported_vault.get("ok")) and bool(preview.get("ok")) and preview.get("proposalCount") == 1 and before_preview_count == after_preview_count and preview_path.exists(),
                    "Obsidian import preview creates a review artifact without mutating the ledger.",
                    evidence={
                        "vaultExportOk": exported_vault.get("ok"),
                        "proposalCount": preview.get("proposalCount"),
                        "eventCountBefore": before_preview_count,
                        "eventCountAfter": after_preview_count,
                        "previewFile": str(preview_path),
                    },
                )
                promoted = core.vault_import_promote(str(preview.get("preview_id") or ""))
                promoted_path = core.home / "reviews" / "obsidian" / "promoted" / f"{preview.get('preview_id')}.json"
                promoted_events = promoted.get("events") or []
                add(
                    "fixture_obsidian_promote_ledgered",
                    bool(promoted.get("ok")) and promoted.get("eventCount") == 1 and promoted_events and promoted_events[0].get("kind") == "obsidian_note_import" and promoted_path.exists(),
                    "Obsidian import promotion writes explicit owner-reviewed ledger events.",
                    evidence={"eventCount": promoted.get("eventCount"), "eventKind": promoted_events[0].get("kind") if promoted_events else None},
                )

                learning_before_count = core.health()["eventCount"]
                learning = core.learning_review(session_id="trust-gate-learning", persist=True)
                learning_after_count = core.health()["eventCount"]
                learning_path = Path(str(learning.get("reviewFile") or ""))
                learning_candidates = learning.get("candidates") or []
                learning_layers = {candidate.get("layer") for candidate in learning_candidates}
                add(
                    "fixture_learning_review_candidate_cards",
                    bool(learning.get("ok"))
                    and learning.get("status") == "PREVIEW"
                    and learning_candidates
                    and "gbrain_page" in learning_layers
                    and any((candidate.get("decision") or {}).get("timelineAction") == "append_evidence" for candidate in learning_candidates)
                    and learning_before_count == learning_after_count
                    and learning_path.exists(),
                    "Nightly learning produces candidate cards, promotion decisions, and a wake-up diff without mutating the ledger.",
                    evidence={
                        "candidateCount": learning.get("candidateCount"),
                        "layerCounts": learning.get("layerCounts"),
                        "wakeUpDiffCount": len(learning.get("wakeUpDiff") or []),
                        "eventCountBefore": learning_before_count,
                        "eventCountAfter": learning_after_count,
                        "reviewFile": str(learning_path),
                    },
                )

                federated = TotalRecallCore(TotalRecallConfig(home=fed_home, enable_lancedb=False, enable_qmd=False))
                federated.ingest(kind="note", text="Federated workspace return policy is thirty-day returns.", session_id="fed", scope="public")
                federated.knowledge_index_rebuild()
                registered = core.federation_register("agent-beta", fed_home, role="hermes-agent", scopes=["public"])
                blocked = core.federation_query("return policy", targets=["agent-beta"], allowed_scopes=["public"])
                allowed = core.federation_query("return policy", targets=["agent-beta"], authorize=True, allowed_scopes=["public"])
                blocked_federation = blocked.get("federation") or {}
                allowed_federation = allowed.get("federation") or {}
                workspaces = allowed_federation.get("workspaces") or []
                add(
                    "fixture_federation_authorization_required",
                    bool(registered.get("ok")) and blocked_federation.get("status") == "AUTHORIZATION_REQUIRED" and not blocked_federation.get("workspaces"),
                    "Federation refuses to read another workspace without explicit authorization.",
                    evidence={"blockedStatus": blocked_federation.get("status"), "workspaceCount": len(blocked_federation.get("workspaces") or [])},
                )
                add(
                    "fixture_federation_workspace_separated",
                    bool(workspaces and workspaces[0].get("citations")) and allowed_federation.get("merged") is False,
                    "Authorized federation returns cited, workspace-separated results without silent merge.",
                    evidence={"authorized": allowed_federation.get("authorized"), "merged": allowed_federation.get("merged"), "workspaceCount": len(workspaces)},
                )

                checkpoint = core.checkpoint(session_id="trust-gate", label="trust_gate_fixture")
                verification = core.verify(session_id="trust-gate")
                bundle = root / "fixture-export.tar.gz"
                exported = core.export_bundle(str(bundle))
                imported = TotalRecallCore(TotalRecallConfig(home=imported_home, enable_lancedb=False, enable_qmd=False))
                imported_result = imported.import_bundle(str(bundle))
                imported_state = imported.reduce_state(write=True)
                add(
                    "fixture_persistence_checkpoint_export_import",
                    bool(checkpoint.get("ok")) and bool(verification.get("ok")) and bool(exported.get("ok")) and bool(imported_result.get("ok")) and imported_state.get("event_count") == core.health().get("eventCount"),
                    "Checkpoint, signed verify, export manifest, and import restore preserve the fixture ledger.",
                    evidence={
                        "verifyStatus": verification.get("status"),
                        "exportFileCount": exported.get("fileCount"),
                        "importedEventCount": imported_state.get("event_count"),
                    },
                )
        except Exception as exc:
            add("fixture_trust_gate_harness_exception", False, f"Trust gate fixture harness failed: {exc}", evidence={"error": str(exc)})
        return checks

    def _trust_gate_export_import_check(self, state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            event_count = int(state.get("event_count") or 0)
            if event_count <= 0:
                return self._trust_gate_check(
                    "real_store_export_import_round_trip",
                    True,
                    "Empty real store has no ledger payload requiring export/import proof yet.",
                    evidence={"eventCount": event_count},
                )
            with tempfile.TemporaryDirectory(prefix="total-recall-real-roundtrip-") as tmp:
                root = Path(tmp)
                bundle = root / "real-store.tar.gz"
                exported = self.export_bundle(str(bundle))
                imported = TotalRecallCore(TotalRecallConfig(home=root / "imported", enable_lancedb=False, enable_qmd=False))
                imported_result = imported.import_bundle(str(bundle))
                imported_state = imported.reduce_state(write=True)
                return self._trust_gate_check(
                    "real_store_export_import_round_trip",
                    bool(exported.get("ok")) and bool(imported_result.get("ok")) and imported_state.get("event_count") == event_count and imported_state.get("last_event_hash") == state.get("last_event_hash"),
                    "Real store can be exported, manifest-verified, imported, and reduced to the same ledger point.",
                    evidence={
                        "exportOk": exported.get("ok"),
                        "importOk": imported_result.get("ok"),
                        "eventCount": event_count,
                        "importedEventCount": imported_state.get("event_count"),
                        "lastEventHash": state.get("last_event_hash"),
                        "importedLastEventHash": imported_state.get("last_event_hash"),
                    },
                )
        except Exception as exc:
            return self._trust_gate_check("real_store_export_import_round_trip", False, f"Real store export/import proof failed: {exc}", evidence={"error": str(exc)})

    def _trust_gate_hermes_bundle_checks(self) -> List[Dict[str, Any]]:
        checks: List[Dict[str, Any]] = []
        try:
            from . import hermes_installer

            generated = hermes_installer.plugin_files()
            yaml_text = str(generated.get("plugin.yaml") or "")
            init_text = str(generated.get("__init__.py") or "")
            missing_generated = sorted(tool for tool in TRUST_GATE_REQUIRED_HERMES_TOOLS if tool not in yaml_text)
            provider_ok = "TotalRecallMemoryProvider" in init_text and "register" in init_text

            repo_source = hermes_installer.repo_plugin_source()
            missing_repo: List[str] = []
            repo_ok = True
            if repo_source is not None:
                repo_yaml = (repo_source / "plugin.yaml").read_text(encoding="utf-8")
                repo_init = (repo_source / "__init__.py").read_text(encoding="utf-8")
                missing_repo = sorted(tool for tool in TRUST_GATE_REQUIRED_HERMES_TOOLS if tool not in repo_yaml)
                repo_ok = not missing_repo and "TotalRecallMemoryProvider" in repo_init and "register" in repo_init

            with tempfile.TemporaryDirectory(prefix="total-recall-hermes-bundle-gate-") as tmp:
                bundle_path = Path(tmp) / "total-recall-hermes-plugin.tar.gz"
                bundled = hermes_installer.bundle_plugin(out=str(bundle_path), force=True)
                bundled_yaml = ""
                names: List[str] = []
                if bundle_path.exists():
                    with tarfile.open(bundle_path, "r:gz") as tar:
                        names = sorted(tar.getnames())
                        member = tar.extractfile("total-recall/plugin.yaml")
                        if member is not None:
                            bundled_yaml = member.read().decode("utf-8")
                missing_bundled = sorted(tool for tool in TRUST_GATE_REQUIRED_HERMES_TOOLS if tool not in bundled_yaml)

            checks.append(
                self._trust_gate_check(
                    "fixture_hermes_plugin_bundle_surface",
                    not missing_generated and provider_ok and repo_ok and bool(bundled.get("ok")) and not missing_bundled,
                    "Hermes plugin generator, repo bundle, and distributable tar expose the required memory-provider tools.",
                    evidence={
                        "requiredTools": sorted(TRUST_GATE_REQUIRED_HERMES_TOOLS),
                        "missingGenerated": missing_generated,
                        "missingRepo": missing_repo,
                        "missingBundled": missing_bundled,
                        "providerEntrypointOk": provider_ok,
                        "repoPluginSource": str(repo_source) if repo_source else None,
                        "bundleOk": bundled.get("ok"),
                        "bundleMembers": names,
                    },
                )
            )
        except Exception as exc:
            checks.append(self._trust_gate_check("fixture_hermes_plugin_bundle_surface", False, f"Hermes plugin bundle proof failed: {exc}", evidence={"error": str(exc)}))
        return checks

    @staticmethod
    def _trust_gate_check(
        name: str,
        ok: bool,
        summary: str,
        *,
        evidence: Optional[Dict[str, Any]] = None,
        severity: str = "required",
    ) -> Dict[str, Any]:
        return {
            "name": name,
            "ok": bool(ok),
            "severity": severity,
            "status": "PASS" if ok else "FAIL",
            "summary": summary,
            "evidence": evidence or {},
            "checked_at": utc_now(),
        }

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

    def sync_status(self, out_dir: str) -> Dict[str, Any]:
        directory = Path(out_dir).expanduser()
        state = self.reduce_state(write=False)
        local_checkpoint = self._latest_checkpoint_summary()
        latest_bundle = self.backup_status(str(directory)).get("latest")
        archive = self._backup_bundle_summary(Path(str(latest_bundle))) if latest_bundle else None
        archive_checkpoint = (archive or {}).get("latestCheckpoint") or {}

        if not archive:
            relation = "local_ahead"
            message = "No backup archive found. Upload a backup before switching machines."
        elif not archive_checkpoint:
            relation = "diverged"
            message = "Latest archive has no checkpoint summary. Inspect the archive before trusting it."
        else:
            local_count = int(state.get("event_count") or 0)
            archive_count = int(archive_checkpoint.get("event_count") or 0)
            local_hash = state.get("last_event_hash")
            archive_hash = archive_checkpoint.get("last_event_hash")
            if local_count == archive_count and local_hash == archive_hash:
                relation = "in_sync"
                message = "Local store and latest archive pin the same ledger point."
            elif local_count > archive_count:
                relation = "local_ahead"
                message = f"Local store is ahead by {local_count - archive_count} event(s). Upload a new backup."
            elif archive_count > local_count:
                relation = "archive_ahead"
                message = f"Latest archive is ahead by {archive_count - local_count} event(s). Download/import before working."
            else:
                relation = "diverged"
                message = "Local and archive event counts match but ledger hashes differ. Do not auto-merge."

        return {
            "ok": True,
            "relation": relation,
            "message": message,
            "local": {
                "stateHash": state.get("state_hash"),
                "eventCount": state.get("event_count"),
                "lastEventHash": state.get("last_event_hash"),
                "latestCheckpoint": local_checkpoint,
            },
            "archive": archive,
            "backupDir": str(directory),
        }

    def backup_run(
        self,
        out_dir: str,
        *,
        keep: int = 14,
        keep_days: Optional[int] = None,
        include_index: bool = False,
        checkpoint: bool = True,
    ) -> Dict[str, Any]:
        directory = Path(out_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        bundle = directory / f"total-recall-backup-{stamp}-{secrets.token_hex(3)}.tar.gz"

        checkpoint_result = self.checkpoint(session_id="backup", label="automatic_backup") if checkpoint else None
        exported = self.export_bundle(str(bundle), include_index=include_index)
        doctor = self.doctor()
        verification = self.verify()
        pruned: List[str] = []
        backups = self._list_backup_files(directory)
        keep_set = set(backups[: max(0, keep)]) if keep > 0 else set(backups)
        cutoff = None
        if keep_days is not None and keep_days >= 0:
            cutoff = datetime.now(timezone.utc).timestamp() - (keep_days * 86400)
        for path in backups:
            too_many = keep > 0 and path not in keep_set
            too_old = cutoff is not None and path.stat().st_mtime < cutoff
            if too_many or too_old:
                path.unlink(missing_ok=True)
                pruned.append(str(path))

        ok = bool(exported.get("ok")) and bool(doctor.get("ok")) and bool(verification.get("ok"))
        payload = {
            "ok": ok,
            "status": "PASS" if ok else "FAIL_CLOSED",
            "checkpoint": checkpoint_result,
            "backup": exported,
            "doctor": doctor,
            "verification": verification,
            "retention": {"keep": keep, "keepDays": keep_days, "pruned": pruned},
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
            "knowledge/index",
            "knowledge/graph",
            "knowledge/synthesis/staging",
            "knowledge/synthesis/runs",
            "knowledge/synthesis/promoted",
            "knowledge/compiled",
            "knowledge/quarantine",
            "knowledge/reports",
            "knowledge/eval",
            "knowledge/providers",
            "reviews/learning",
            "reviews/obsidian/promoted",
            "federation",
        ):
            (self.home / rel).mkdir(parents=True, exist_ok=True)
        self.ledger_file.touch(exist_ok=True)
        self.lock_file.touch(exist_ok=True)

    def _knowledge(self):
        from .knowledge import KnowledgeEngine

        return KnowledgeEngine(self)

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

    def _latest_checkpoint_summary(self) -> Optional[Dict[str, Any]]:
        latest = self._latest_file(self.home / "checkpoints", "*.json")
        if not latest:
            return None
        payload = self._read_json(latest)
        return {
            "path": str(latest),
            "checkpoint_id": payload.get("checkpoint_id"),
            "created_at": payload.get("created_at"),
            "event_count": payload.get("event_count"),
            "last_event_hash": payload.get("last_event_hash"),
            "state_hash": payload.get("state_hash"),
            "checkpoint_hash": payload.get("checkpoint_hash"),
        }

    def _backup_bundle_summary(self, bundle: Path) -> Dict[str, Any]:
        if not bundle.exists():
            return {"ok": False, "error": "bundle_not_found", "bundle": str(bundle)}
        checkpoints: List[Dict[str, Any]] = []
        manifest: Dict[str, Any] = {}
        with tarfile.open(bundle, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name == "MANIFEST.json":
                    source = tar.extractfile(member)
                    if source:
                        manifest = json.loads(source.read().decode("utf-8"))
                if member.isfile() and member.name.startswith("checkpoints/") and member.name.endswith(".json"):
                    source = tar.extractfile(member)
                    if not source:
                        continue
                    payload = json.loads(source.read().decode("utf-8"))
                    checkpoints.append({
                        "path": member.name,
                        "checkpoint_id": payload.get("checkpoint_id"),
                        "created_at": payload.get("created_at"),
                        "event_count": payload.get("event_count"),
                        "last_event_hash": payload.get("last_event_hash"),
                        "state_hash": payload.get("state_hash"),
                        "checkpoint_hash": payload.get("checkpoint_hash"),
                    })
        latest_checkpoint = max(checkpoints, key=lambda item: str(item.get("created_at") or "")) if checkpoints else None
        return {
            "ok": True,
            "bundle": str(bundle),
            "bytes": bundle.stat().st_size,
            "modified": datetime.fromtimestamp(bundle.stat().st_mtime, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "manifest": {
                "schema": manifest.get("schema"),
                "version": manifest.get("version"),
                "created_at": manifest.get("created_at"),
                "fileCount": len(manifest.get("files") or []),
            },
            "latestCheckpoint": latest_checkpoint,
        }

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

    def _resolve_total_recall_home(self, path: Path) -> Path:
        candidate = path.expanduser()
        if (candidate / "ledger" / "events.jsonl").exists() or candidate.name == ".total-recall":
            return candidate
        if (candidate / ".total-recall" / "ledger" / "events.jsonl").exists():
            return candidate / ".total-recall"
        if (candidate / "total-recall" / "ledger" / "events.jsonl").exists():
            return candidate / "total-recall"
        return candidate

    def _federation_registry(self) -> Dict[str, Any]:
        path = self.home / "federation" / "targets.json"
        if path.exists():
            try:
                payload = self._read_json(path)
                payload.setdefault("schema", FEDERATION_SCHEMA_VERSION)
                payload.setdefault("targets", {})
                return payload
            except Exception:
                pass
        return {"schema": FEDERATION_SCHEMA_VERSION, "created_at": utc_now(), "targets": {}}

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
