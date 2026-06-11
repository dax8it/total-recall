from __future__ import annotations

import base64
import fnmatch
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


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
PORTABLE_CLONE_SCHEMA_VERSION = "total-recall-portable-clone-v1"
ENCRYPTED_BACKUP_SCHEMA_VERSION = "total-recall-encrypted-backup-v1"
LOOP_EVENT_SCHEMA_VERSION = "total-recall-loop-event-v1"
RESUME_PACKET_SCHEMA_VERSION = "total-recall-resume-packet-v1"
HANDOFF_BOOTSTRAP_SCHEMA_VERSION = "total-recall-handoff-bootstrap-v1"
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
    "total_recall_loop_inbox",
    "total_recall_loop_start",
    "total_recall_loop_note",
    "total_recall_loop_verify",
    "total_recall_loop_complete",
    "total_recall_handoff_export",
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


def _redact_secret_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"hf_[A-Za-z0-9_\-]{12,}", "[redacted-hf-token]", text)
    text = re.sub(r"(?i)(token|authorization|api[_-]?key)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    return text


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
    def device_private_key_file(self) -> Path:
        return self.home / "keys" / "device.ed25519"

    @property
    def device_public_key_file(self) -> Path:
        return self.home / "keys" / "device.ed25519.pub"

    @property
    def device_x25519_private_key_file(self) -> Path:
        return self.home / "keys" / "device.x25519"

    @property
    def device_x25519_public_key_file(self) -> Path:
        return self.home / "keys" / "device.x25519.pub"

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
                "origin": self._event_origin(source=source),
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
                base = {
                    **event_base,
                    "origin": self._event_origin(source=str(event_base.get("source") or "")),
                    "prev_hash": previous_hash,
                }
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
                "origin": event.get("origin") or {},
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
            payload = self._checkpoint_locked(session_id=session_id, label=label)
        if _safe_id(label) == "session_end" and payload.get("ok"):
            try:
                payload["resumePacket"] = self.write_resume_packet(
                    session_id=session_id,
                    checkpoint=payload.get("checkpoint") or {},
                    checkpoint_file=str(payload.get("checkpointFile") or ""),
                    anchor=payload.get("anchor") or {},
                )
            except Exception as exc:
                payload["resumePacket"] = {"ok": False, "error": str(exc)}
        return payload

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
        receipt = self._append_checkpoint_receipt(checkpoint)
        report = self._write_report(
            "checkpoint",
            checkpoint_id,
            {
                "ok": True,
                "checkpoint": checkpoint,
                "checkpointFile": str(checkpoint_path),
                "anchor": anchor,
                "receipt": receipt,
            },
        )
        payload = {
            "ok": True,
            "checkpoint": checkpoint,
            "checkpointFile": str(checkpoint_path),
            "anchor": anchor,
            "receipt": receipt,
            "report": report,
        }
        return payload

    def verify(self, *, session_id: Optional[str] = None, checkpoint_file: Optional[str] = None, receipts: bool = False) -> Dict[str, Any]:
        with self._locked():
            return self._verify_locked(session_id=session_id, checkpoint_file=checkpoint_file, receipts=receipts)

    def _verify_locked(self, *, session_id: Optional[str] = None, checkpoint_file: Optional[str] = None, receipts: bool = False) -> Dict[str, Any]:
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
        if receipts:
            receipt_check = self._verify_receipts_against_events(current_state or state)
            details["receipts"] = receipt_check
            if not receipt_check.get("ok"):
                failures.append("receipt_lineage_mismatch")

        return self._verification_result(not failures, failures, details, session_id=session_id)

    def rehydrate(
        self,
        *,
        session_id: str = "default",
        query: str = "",
        max_results: int = 8,
        mode: str = "keyword",
        char_budget: int = 8000,
    ) -> Dict[str, Any]:
        verification = self.verify(session_id=session_id)
        if not verification.get("ok"):
            return {
                "ok": False,
                "status": "FAIL_CLOSED",
                "error": "verification failed; refusing rehydrate",
                "verification": verification,
            }
        if mode == "resume":
            resume = self._rehydrate_resume(
                session_id=session_id,
                verification=verification,
                char_budget=char_budget,
            )
            if resume.get("ok"):
                return resume
            if resume.get("status") == "FAIL_CLOSED":
                return resume
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
            "mode": "keyword",
            "query": search_query,
            "context_block": context_block,
            "verification": verification,
            "search": search,
        }
        payload["report"] = self._write_report("rehydrate", _safe_id(session_id), payload)
        return payload

    def handoff_export(self, *, session_id: str = "default", turns: Optional[int] = None) -> Dict[str, Any]:
        checkpoint = self.checkpoint(session_id=session_id, label="handoff_export")
        if not checkpoint.get("ok"):
            return {"ok": False, "error": "checkpoint_failed", "checkpoint": checkpoint}
        packet = self.write_resume_packet(
            session_id=session_id,
            turns=turns,
            checkpoint=checkpoint.get("checkpoint") or {},
            checkpoint_file=str(checkpoint.get("checkpointFile") or ""),
            anchor=checkpoint.get("anchor") or {},
        )
        return {
            "ok": bool(packet.get("ok")),
            "schema": RESUME_PACKET_SCHEMA_VERSION,
            "session_id": session_id,
            "checkpoint": checkpoint,
            "packet": packet,
            "packetFile": packet.get("packetFile"),
        }

    def handoff_issue(
        self,
        *,
        target: str,
        session_id: str = "default",
        turns: Optional[int] = None,
        ttl_seconds: int = 3600,
        passphrase: str = "",
    ) -> Dict[str, Any]:
        if not str(target or "").strip():
            return {"ok": False, "error": "target_required"}
        release_before = self.lease_release(target=target)
        if not self._handoff_release_allowed(release_before):
            return {"ok": False, "error": "lease_release_failed", "release": release_before}

        handoff_export = self.handoff_export(session_id=session_id, turns=turns)
        if not handoff_export.get("ok"):
            return {"ok": False, "error": "resume_packet_failed", "handoffExport": handoff_export}

        pushed = self.backup_push(target=target, passphrase=passphrase)
        if not pushed.get("ok"):
            return {"ok": False, "error": "push_failed", "release": release_before, "handoffExport": handoff_export, "push": pushed}

        release_after = self.lease_release(target=target)
        if not release_after.get("ok"):
            return {
                "ok": False,
                "error": "lease_release_after_push_failed",
                "release": release_before,
                "handoffExport": handoff_export,
                "push": pushed,
                "releaseAfterPush": release_after,
            }

        handoff_id = self._new_id("handoff")
        handoff_dir = self.home / "handoff"
        json_path = handoff_dir / f"{handoff_id}.json"
        script_path = handoff_dir / f"{handoff_id}.sh"
        packet_file = Path(str(handoff_export.get("packetFile") or ""))
        latest = (pushed.get("head") or {}).get("latest") or {}
        issued_at = utc_now()
        payload = {
            "ok": True,
            "schema": HANDOFF_BOOTSTRAP_SCHEMA_VERSION,
            "handoff_id": handoff_id,
            "created_at": issued_at,
            "session_id": session_id,
            "turns": turns,
            "target": target,
            "remote": pushed.get("target"),
            "store_id": self.store_id(),
            "issued_by_device_id": self.device_id(),
            "lease": {
                "releaseBeforePush": self._summarize_operation(release_before),
                "releaseAfterPush": self._summarize_operation(release_after),
            },
            "bundle": {
                "name": latest.get("bundle"),
                "manifest": latest.get("manifest"),
                "sha256": latest.get("bundle_sha256"),
                "checkpoint_id": latest.get("checkpoint_id"),
                "event_count": latest.get("event_count"),
                "last_event_hash": latest.get("last_event_hash"),
                "device_id": latest.get("device_id"),
            },
            "packet": {
                "path": str(packet_file) if str(packet_file) else "",
                "relative_path": self._home_relative(packet_file),
                "packet_id": ((handoff_export.get("packet") or {}).get("packet") or {}).get("packet_id"),
            },
            "commands": [
                f"total-recall backup pull --target {shlex.quote(target)}",
                "total-recall verify --receipts",
                "total-recall trust verify --format text",
                f"total-recall lease acquire --target {shlex.quote(target)} --ttl {max(1, int(ttl_seconds or 3600))}",
                f"total-recall rehydrate --session-id {shlex.quote(session_id)} --mode resume --char-budget 12000",
            ],
            "instructions": [
                "Set TOTAL_RECALL_HOME for the accepting store before running the script.",
                "Set TOTAL_RECALL_BACKUP_PASSPHRASE or approve this device before pulling encrypted backups.",
                "Trust only the pulled ledger after verify and trust verify pass; the handoff JSON and packet are derived artifacts.",
            ],
            "push": self._summarize_operation(pushed),
        }
        self._write_json(json_path, payload)
        script = self._handoff_bootstrap_script(target=target, session_id=session_id, ttl_seconds=ttl_seconds)
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o700)
        payload["handoffFile"] = str(json_path)
        payload["bootstrapScript"] = str(script_path)
        self._write_json(json_path, payload)
        return payload

    def handoff_accept(
        self,
        handoff_file: str,
        *,
        ttl_seconds: int = 3600,
        char_budget: int = 12000,
        run_trust_gate: bool = True,
        passphrase: str = "",
    ) -> Dict[str, Any]:
        path = Path(handoff_file).expanduser()
        if not path.exists():
            return {"ok": False, "error": "handoff_file_not_found", "handoffFile": str(path)}
        handoff = self._read_json(path)
        if handoff.get("schema") != HANDOFF_BOOTSTRAP_SCHEMA_VERSION:
            return {"ok": False, "error": "invalid_handoff_schema", "handoffFile": str(path), "schema": handoff.get("schema")}
        target = str(handoff.get("target") or "")
        session_id = str(handoff.get("session_id") or "default")
        pulled = self.backup_pull(target=target, passphrase=passphrase)
        if not pulled.get("ok") and pulled.get("status") != "IN_SYNC":
            return {"ok": False, "status": "FAIL_CLOSED", "error": "pull_failed", "handoff": handoff, "pull": pulled}
        verification = self.verify(receipts=True)
        if not verification.get("ok"):
            return {"ok": False, "status": "FAIL_CLOSED", "error": "verify_receipts_failed", "handoff": handoff, "pull": pulled, "verification": verification}
        trust = self.trust_gate_run() if run_trust_gate else {"ok": True, "status": "SKIPPED_INTERNAL_FIXTURE"}
        if not trust.get("ok"):
            return {
                "ok": False,
                "status": "FAIL_CLOSED",
                "error": "trust_gate_failed",
                "handoff": handoff,
                "pull": pulled,
                "verification": verification,
                "trustGate": trust,
            }
        lease = self.lease_acquire(target=target, ttl_seconds=ttl_seconds)
        if not lease.get("ok"):
            return {
                "ok": False,
                "status": "FAIL_CLOSED",
                "error": "lease_acquire_failed",
                "handoff": handoff,
                "pull": pulled,
                "verification": verification,
                "trustGate": trust,
                "lease": lease,
            }
        resume = self.rehydrate(session_id=session_id, mode="resume", char_budget=char_budget)
        ok = bool(resume.get("ok"))
        return {
            "ok": ok,
            "status": "PASS" if ok else "FAIL_CLOSED",
            "schema": HANDOFF_BOOTSTRAP_SCHEMA_VERSION,
            "handoff": handoff,
            "handoffFile": str(path),
            "pull": pulled,
            "verification": verification,
            "trustGate": trust,
            "lease": lease,
            "resume": resume,
            "resumeBlock": resume.get("context_block"),
        }

    def write_resume_packet(
        self,
        *,
        session_id: str = "default",
        turns: Optional[int] = None,
        checkpoint: Optional[Dict[str, Any]] = None,
        checkpoint_file: str = "",
        anchor: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._write_resume_packet_unlocked(
            session_id=session_id,
            turns=turns,
            checkpoint=checkpoint,
            checkpoint_file=checkpoint_file,
            anchor=anchor,
        )

    def _write_resume_packet_unlocked(
        self,
        *,
        session_id: str = "default",
        turns: Optional[int] = None,
        checkpoint: Optional[Dict[str, Any]] = None,
        checkpoint_file: str = "",
        anchor: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        safe_session = _safe_id(session_id or "default")
        turn_count = max(1, int(turns if turns is not None else os.getenv("TOTAL_RECALL_RESUME_PACKET_TURNS", "30")))
        events = self._read_events(verify_chain=True)
        state = self._state_from_events(events)
        if checkpoint is None:
            checkpoint_path = self._select_checkpoint(session_id)
            checkpoint = self._read_json(checkpoint_path) if checkpoint_path and checkpoint_path.exists() else {}
            checkpoint_file = str(checkpoint_path or checkpoint_file or "")
        anchor = anchor or {}
        if checkpoint and not anchor:
            anchor_path = self.home / "anchors" / f"{checkpoint.get('checkpoint_id')}.json"
            if anchor_path.exists():
                anchor = self._read_json(anchor_path)
                anchor["anchorFile"] = str(anchor_path)

        recent_turns = [
            {
                "event_id": event.get("event_id"),
                "timestamp": event.get("timestamp"),
                "hash": event.get("hash"),
                "text": event.get("text") or "",
            }
            for event in events
            if event.get("kind") == "turn" and (event.get("session_id") or "default") == (session_id or "default")
        ][-turn_count:]
        open_loops = (self.loop_inbox().get("loops") or [])[:20]
        freshness = self._resume_freshness_summary()
        compiled_truth_excerpt = self._compiled_truth_excerpt(limit=4000)
        next_actions = self._extract_next_actions(recent_turns=recent_turns, open_loops=open_loops)
        created_at = utc_now()
        packet_id = self._new_id(f"packet_{safe_session}")
        packet = {
            "schema": RESUME_PACKET_SCHEMA_VERSION,
            "packet_id": packet_id,
            "created_at": created_at,
            "session_id": session_id or "default",
            "checkpoint_id": (checkpoint or {}).get("checkpoint_id"),
            "anchor_id": (anchor or {}).get("anchor_id") or (anchor or {}).get("checkpoint_id"),
            "anchor_file": (anchor or {}).get("anchorFile") or (anchor or {}).get("anchor_file") or "",
            "ledger": {
                "event_count": state.get("event_count", 0),
                "last_event_hash": state.get("last_event_hash"),
            },
            "recent_turns": recent_turns,
            "open_loops": open_loops,
            "freshness": freshness,
            "compiled_truth_excerpt": compiled_truth_excerpt,
            "environment": self._environment_fingerprint(),
            "next_actions": next_actions,
        }
        packet_path = self.home / "continuation" / safe_session / f"packet_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}.json"
        self._write_json(packet_path, packet)
        return {
            "ok": True,
            "schema": RESUME_PACKET_SCHEMA_VERSION,
            "packet": packet,
            "packetFile": str(packet_path),
            "recentTurnCount": len(recent_turns),
            "nextActionCount": len(next_actions),
        }

    def _rehydrate_resume(
        self,
        *,
        session_id: str,
        verification: Dict[str, Any],
        char_budget: int,
    ) -> Dict[str, Any]:
        try:
            events = self._read_events(verify_chain=True)
        except Exception as exc:
            return {
                "ok": False,
                "status": "FAIL_CLOSED",
                "error": f"ledger invalid; refusing resume rehydrate: {exc}",
                "verification": verification,
            }
        packet_info = self._newest_resume_packet_for_chain(session_id=session_id, events=events)
        if not packet_info:
            return {"ok": False, "status": "NO_RESUME_PACKET", "session_id": session_id, "verification": verification}
        packet = packet_info["packet"]
        context_block = self._resume_context_block(
            packet,
            verification=verification,
            packet_file=str(packet_info["path"]),
            char_budget=char_budget,
        )
        payload = {
            "ok": True,
            "status": "PASS",
            "mode": "resume",
            "session_id": session_id,
            "context_block": context_block,
            "packet": packet,
            "packetFile": str(packet_info["path"]),
            "verification": verification,
        }
        payload["report"] = self._write_report("rehydrate", _safe_id(session_id), payload)
        return payload

    def _newest_resume_packet_for_chain(self, *, session_id: str, events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        hashes = {str(event.get("hash") or "") for event in events if event.get("hash")}
        session_dir = self.home / "continuation" / _safe_id(session_id or "default")
        if not session_dir.exists():
            return None
        packets = sorted(session_dir.glob("packet_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in packets:
            try:
                packet = self._read_json(path)
            except Exception:
                continue
            if packet.get("schema") != RESUME_PACKET_SCHEMA_VERSION:
                continue
            last_hash = str(((packet.get("ledger") or {}).get("last_event_hash")) or "")
            if last_hash and last_hash in hashes:
                return {"packet": packet, "path": path}
        return None

    def _resume_context_block(
        self,
        packet: Dict[str, Any],
        *,
        verification: Dict[str, Any],
        packet_file: str,
        char_budget: int,
    ) -> str:
        budget = max(1000, int(char_budget or 8000))
        header = [
            "[Total Recall Resume Packet Authority]",
            "status: PASS",
            f"session_id: {packet.get('session_id')}",
            f"packet: {packet_file}",
            f"checkpoint: {packet.get('checkpoint_id')} ({verification.get('checkpointFile')})",
            f"anchor: {packet.get('anchor_id')} ({packet.get('anchor_file') or verification.get('anchorFile')})",
            f"ledger: {((packet.get('ledger') or {}).get('event_count'))} events @ {((packet.get('ledger') or {}).get('last_event_hash'))}",
            "",
        ]
        static_sections = header + ["Recent verbatim turn events:"]
        lines = list(static_sections)
        turn_lines: List[str] = []
        for turn in reversed(packet.get("recent_turns") or []):
            turn_lines.extend(
                [
                    f"- {turn.get('timestamp')} {turn.get('event_id')} {turn.get('hash')}",
                    str(turn.get("text") or ""),
                    "",
                ]
            )
            candidate = "\n".join(lines + turn_lines)
            if len(candidate) > budget and len(turn_lines) > 3:
                turn_lines = turn_lines[:-3]
                break
        if turn_lines:
            lines.extend(turn_lines)
        else:
            lines.append("- No turn events found in the selected resume packet.")
        lines.extend(["", "Open loops:"])
        open_loops = packet.get("open_loops") or []
        if open_loops:
            for loop in open_loops[:8]:
                lines.append(f"- {loop.get('loop_id')}: {loop.get('goal')} [{loop.get('phase')}]")
        else:
            lines.append("- No open loops.")
        lines.extend(["", "Deterministic next actions:"])
        next_actions = packet.get("next_actions") or []
        if next_actions:
            for action in next_actions[:12]:
                lines.append(f"- {action}")
        else:
            lines.append("- No explicit next/todo/blocker/decision lines extracted.")
        freshness = packet.get("freshness") or {}
        if freshness:
            lines.extend(["", "Freshness summary:", canonical_json(freshness)])
        truth = str(packet.get("compiled_truth_excerpt") or "").strip()
        if truth:
            lines.extend(["", "Compiled-truth excerpt:", truth[:4000]])
        return "\n".join(lines)

    def _resume_freshness_summary(self) -> Dict[str, Any]:
        try:
            report = self.knowledge_freshness_report()
            return {
                "ok": bool(report.get("ok")),
                "asOf": report.get("asOf"),
                "counts": report.get("counts") or {},
                "itemCount": len(report.get("items") or []),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _compiled_truth_excerpt(self, *, limit: int) -> str:
        try:
            truth = self.knowledge_compiled_truth_show(format_="md")
            text = str(truth.get("text") or "")
            return text[: max(0, int(limit))]
        except Exception:
            return ""

    def _extract_next_actions(self, *, recent_turns: List[Dict[str, Any]], open_loops: List[Dict[str, Any]]) -> List[str]:
        patterns = re.compile(r"\b(next|todo|to do|blocker|blocked|decision|decide|follow[- ]?up|action item)\b", re.IGNORECASE)
        actions: List[str] = []
        seen: set[str] = set()
        for turn in recent_turns:
            for line in str(turn.get("text") or "").splitlines():
                cleaned = _one_line(line, limit=240)
                if not cleaned or not patterns.search(cleaned):
                    continue
                key = cleaned.lower()
                if key in seen:
                    continue
                seen.add(key)
                actions.append(cleaned)
                if len(actions) >= 20:
                    break
            if len(actions) >= 20:
                break
        for loop in open_loops:
            goal = _one_line(str(loop.get("goal") or ""), limit=200)
            if not goal:
                continue
            item = f"Open loop {loop.get('loop_id')}: {goal}"
            key = item.lower()
            if key not in seen:
                seen.add(key)
                actions.append(item)
            if len(actions) >= 24:
                break
        return actions

    def _environment_fingerprint(self) -> Dict[str, Any]:
        repo = ""
        branch = ""
        try:
            repo = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
            ).stdout.strip()
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
            ).stdout.strip()
        except Exception:
            pass
        return {
            "cwd": str(Path.cwd()),
            "git_repo": repo,
            "git_branch": branch,
            "hermes_profile": os.getenv("HERMES_PROFILE", ""),
            "device_id": self.device_id(),
            "host": platform.node(),
        }

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

    def export_bundle(self, out: str, *, include_index: bool = False, include_keys: bool = False) -> Dict[str, Any]:
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
            "continuation",
            "handoff",
            "devices",
            "reviews",
            "federation",
        ]
        if include_keys:
            include_dirs.append("keys")
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
            "include_keys": include_keys,
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
            "includeKeys": include_keys,
            "warnings": [] if include_keys else ["keys_excluded_by_default"],
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
                for rel_dir in ("ledger", "state", "checkpoints", "anchors", "reports", "incidents", "external-memory", "continuation", "handoff", "keys", "devices", "index", "reviews", "federation"):
                    shutil.rmtree(self.home / rel_dir, ignore_errors=True)
            self._ensure_layout()
            for item in manifest.get("files", []):
                rel = Path(str(item.get("path") or ""))
                src = tmp_home / rel
                dest = self.home / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

        verification = self.verify()
        re_anchor = None
        checkpoint_after_re_anchor = None
        if verification.get("ok"):
            checkpoint = (verification.get("checkpoint") or {})
            re_anchor = self._append_re_anchor_event(
                restored_checkpoint_id=str(checkpoint.get("checkpoint_id") or ""),
                restored_last_event_hash=str(verification.get("currentLastEventHash") or checkpoint.get("last_event_hash") or ""),
                source_bundle_sha256=self._file_sha256(bundle_path),
                source=f"import:{bundle_path.name}",
            )
            checkpoint_after_re_anchor = self.checkpoint(session_id="re-anchor", label="import_bundle")
            verification = self.verify(session_id="re-anchor")
        return {
            "ok": bool(verification.get("ok")),
            "bundle": str(bundle_path),
            "home": str(self.home),
            "fileCount": len(manifest.get("files", [])),
            "verification": verification,
            "reAnchor": re_anchor,
            "checkpoint": checkpoint_after_re_anchor,
        }

    def portable_clone_export(
        self,
        *,
        out_dir: str | Path,
        passphrase: str = "",
        provider: str = "huggingface",
        repo_id: str = "",
        upload: bool = False,
        include_index: bool = False,
    ) -> Dict[str, Any]:
        """Create an encrypted portable-clone bundle for remote storage.

        This is deliberately encryption-first: remote providers only ever see an
        AES-GCM ciphertext envelope plus a non-secret manifest. The plaintext
        Total Recall export is written only to a temporary directory.
        """
        secret = passphrase or os.getenv("TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE", "")
        if not secret:
            return {"ok": False, "error": "passphrase_required", "schema": PORTABLE_CLONE_SCHEMA_VERSION}
        provider_id = _safe_id(provider or "huggingface")
        if provider_id != "huggingface" and upload:
            return {"ok": False, "error": "provider_upload_not_supported", "provider": provider_id}

        out_path = Path(out_dir).expanduser()
        out_path.mkdir(parents=True, exist_ok=True)
        checkpoint = self.checkpoint(session_id="portable-clone", label="portable_clone_export")
        state = self.reduce_state(write=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        clone_id = f"clone_{stamp}_{secrets.token_hex(4)}"
        encrypted_path = out_path / f"total-recall-portable-clone-{stamp}-{secrets.token_hex(3)}.tar.gz.enc"
        manifest_path = encrypted_path.with_suffix(encrypted_path.suffix + ".manifest.json")

        with tempfile.TemporaryDirectory(prefix="total-recall-portable-clone.") as tmpdir:
            plaintext_bundle = Path(tmpdir) / "total-recall-export.tar.gz"
            exported = self.export_bundle(str(plaintext_bundle), include_index=include_index, include_keys=True)
            plaintext = plaintext_bundle.read_bytes()

        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        iterations = 390_000
        key = self._portable_clone_key(secret, salt=salt, iterations=iterations)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
        manifest = {
            "schema": PORTABLE_CLONE_SCHEMA_VERSION,
            "cloneId": clone_id,
            "createdAt": utc_now(),
            "version": VERSION,
            "provider": {
                "id": provider_id,
                "repoId": repo_id or None,
                "type": "dataset" if provider_id == "huggingface" else "remote",
                "storageContract": "encrypted-bundle-only",
            },
            "encryption": {
                "algorithm": "AES-256-GCM/PBKDF2-SHA256",
                "kdf": "PBKDF2HMAC-SHA256",
                "iterations": iterations,
                "salt": base64.b64encode(salt).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
            },
            "plaintext": {
                "sha256": hashlib.sha256(plaintext).hexdigest(),
                "bytes": len(plaintext),
                "export": exported,
            },
            "ledger": {
                "eventCount": state.get("event_count"),
                "lastEventHash": state.get("last_event_hash"),
                "stateHash": state.get("state_hash"),
                "latestCheckpoint": (checkpoint.get("checkpoint") or {}),
            },
            "restore": {
                "command": f"total-recall portable-clone restore {encrypted_path.name} --replace",
                "requiresPassphraseEnv": "TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE",
                "postRestoreGate": "total-recall verify && total-recall trust verify",
            },
        }
        envelope = {
            "schema": PORTABLE_CLONE_SCHEMA_VERSION,
            "manifest": manifest,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        encrypted_path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest["encrypted"] = {
            "sha256": self._file_sha256(encrypted_path),
            "bytes": encrypted_path.stat().st_size,
            "path": encrypted_path.name,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        upload_result: Optional[Dict[str, Any]] = None
        status = "READY_FOR_UPLOAD"
        ok = True
        if upload:
            upload_result = self._portable_clone_hf_upload(
                repo_id=repo_id,
                encrypted_path=encrypted_path,
                manifest_path=manifest_path,
            )
            ok = bool(upload_result.get("ok"))
            status = "UPLOADED" if ok else "UPLOAD_FAILED"

        payload = {
            "ok": ok,
            "schema": PORTABLE_CLONE_SCHEMA_VERSION,
            "status": status,
            "cloneId": clone_id,
            "provider": manifest["provider"],
            "encryptedBundle": str(encrypted_path),
            "manifestFile": str(manifest_path),
            "ledger": manifest["ledger"],
            "upload": upload_result,
            "nextSteps": [
                "Upload the .enc bundle and manifest to a private Hugging Face dataset or bucket.",
                "On a new machine, download both files and run portable-clone restore with the passphrase in TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE.",
                "After restore, run verify, trust verify, and rebuild derived search catalogs before trusting the clone.",
            ],
        }
        payload["report"] = self._write_report("portable_clone", clone_id, payload)
        return payload

    def portable_clone_restore(
        self,
        bundle: str | Path,
        *,
        passphrase: str = "",
        replace: bool = False,
    ) -> Dict[str, Any]:
        secret = passphrase or os.getenv("TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE", "")
        if not secret:
            return {"ok": False, "error": "passphrase_required", "schema": PORTABLE_CLONE_SCHEMA_VERSION}
        encrypted_path = Path(bundle).expanduser()
        if not encrypted_path.exists():
            return {"ok": False, "error": "encrypted_bundle_not_found", "bundle": str(encrypted_path)}
        try:
            envelope = json.loads(encrypted_path.read_text(encoding="utf-8"))
            manifest = envelope.get("manifest") or {}
            encryption = manifest.get("encryption") or {}
            salt = base64.b64decode(encryption.get("salt") or "")
            nonce = base64.b64decode(encryption.get("nonce") or "")
            iterations = int(encryption.get("iterations") or 390_000)
            ciphertext = base64.b64decode(envelope.get("ciphertext") or "")
            key = self._portable_clone_key(secret, salt=salt, iterations=iterations)
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        except (InvalidTag, ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": "decrypt_failed", "bundle": str(encrypted_path), "detail": exc.__class__.__name__}

        expected_hash = ((manifest.get("plaintext") or {}).get("sha256") or "")
        actual_hash = hashlib.sha256(plaintext).hexdigest()
        if expected_hash and actual_hash != expected_hash:
            return {"ok": False, "error": "plaintext_hash_mismatch", "expected": expected_hash, "actual": actual_hash}

        with tempfile.TemporaryDirectory(prefix="total-recall-portable-restore.") as tmpdir:
            plaintext_bundle = Path(tmpdir) / "total-recall-export.tar.gz"
            plaintext_bundle.write_bytes(plaintext)
            imported = self.import_bundle(str(plaintext_bundle), replace=replace)

        verification = imported.get("verification") or self.verify()
        return {
            "ok": bool(imported.get("ok")) and bool(verification.get("ok")),
            "schema": PORTABLE_CLONE_SCHEMA_VERSION,
            "status": "PASS" if imported.get("ok") and verification.get("ok") else "FAIL_CLOSED",
            "bundle": str(encrypted_path),
            "home": str(self.home),
            "manifest": manifest,
            "import": imported,
            "verification": verification,
            "nextSteps": [
                "Run total-recall trust verify.",
                "Run total-recall knowledge index rebuild and graph rebuild if this clone will answer semantic queries.",
                "Point Hermes profile memory.provider at this restored Total Recall home only after verify/trust gates pass.",
            ],
        }

    def loop_start(
        self,
        *,
        goal: str,
        project: str = "",
        agent: str = "",
        worktree: str = "",
        phase: str = "discovery",
        evidence: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        if not str(goal or "").strip():
            return {"ok": False, "error": "goal_required", "schema": LOOP_EVENT_SCHEMA_VERSION}
        loop_id = self._new_id("loop")
        metadata = {
            "schema": LOOP_EVENT_SCHEMA_VERSION,
            "loop_id": loop_id,
            "loop_event": "start",
            "goal": str(goal).strip(),
            "project": project or "",
            "agent": agent or "",
            "worktree": worktree or "",
            "phase": phase or "discovery",
            "status": "active",
            "evidence": list(evidence or []),
        }
        result = self.ingest(
            kind="loop",
            text=f"Loop started: {metadata['goal']}",
            session_id=f"loop:{loop_id}",
            source="loop:start",
            metadata=metadata,
        )
        loop = self._loop_index().get(loop_id, {})
        self._write_loop_index()
        return {"ok": True, "schema": LOOP_EVENT_SCHEMA_VERSION, "loop": loop, "event": result.get("event")}

    def loop_note(
        self,
        loop_id: str,
        *,
        text: str,
        phase: str = "progress",
        evidence: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        return self._record_loop_event(loop_id, "note", text=text, phase=phase, evidence=evidence)

    def loop_verify(
        self,
        loop_id: str,
        *,
        status: str,
        summary: str = "",
        evidence: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        verdict = str(status or "").upper() or "UNKNOWN"
        text = summary or f"Loop verification: {verdict}"
        return self._record_loop_event(
            loop_id,
            "verify",
            text=text,
            phase="verified",
            evidence=evidence,
            extra={"verification": {"status": verdict, "summary": summary or text}},
        )

    def loop_complete(
        self,
        loop_id: str,
        *,
        status: str = "DONE",
        summary: str = "",
        evidence: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        result_status = str(status or "DONE").upper()
        final_status = "cancelled" if result_status in {"CANCELLED", "FAILED", "FAIL"} else "completed"
        return self._record_loop_event(
            loop_id,
            "complete",
            text=summary or f"Loop completed: {result_status}",
            phase="complete",
            evidence=evidence,
            extra={"status": final_status, "result": result_status, "summary": summary},
        )

    def loop_inbox(self, *, include_completed: bool = False, agent: str = "", project: str = "") -> Dict[str, Any]:
        loops = list(self._loop_index().values())
        if not include_completed:
            loops = [item for item in loops if item.get("status") == "active"]
        if agent:
            loops = [item for item in loops if item.get("agent") == agent]
        if project:
            loops = [item for item in loops if item.get("project") == project]
        loops.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        payload = {"ok": True, "schema": LOOP_EVENT_SCHEMA_VERSION, "count": len(loops), "loops": loops}
        self._write_loop_index()
        return payload

    def doctor(self) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []

        def add(name: str, ok: bool, **details: Any) -> None:
            checks.append({"name": name, "ok": ok, **details})

        required_dirs = ["ledger", "state", "checkpoints", "anchors", "reports", "incidents", "external-memory", "continuation", "index", "keys", "devices", "knowledge", "reviews", "federation", "portable-clones", "loops"]
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
                first_origin = source_events[0].get("origin") or {}
                device_id = str(first_origin.get("device_id") or "")
                device_path = core.home / "devices" / f"device_{device_id}.json"
                add(
                    "fixture_event_origin_device_identity",
                    bool(device_id)
                    and first_origin.get("harness") == "cli"
                    and bool(first_origin.get("host"))
                    and device_path.exists()
                    and core.device_id() == device_id
                    and core.device_private_key_file != core.ed25519_private_key_file,
                    "New ledger events carry a hashed origin with a self-registered device key distinct from the store anchor key.",
                    evidence={
                        "deviceId": device_id,
                        "harness": first_origin.get("harness"),
                        "deviceFile": str(device_path),
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
                    bool(checkpoint.get("ok")) and bool(verification.get("ok")) and bool(exported.get("ok")) and bool(imported_result.get("ok")) and imported_state.get("event_count") == int(core.health().get("eventCount") or 0) + 1 and ((imported_result.get("reAnchor") or {}).get("event") or {}).get("kind") == "re_anchor",
                    "Checkpoint, signed verify, export manifest, import restore, and re-anchor preserve the fixture ledger with an explicit local restore event.",
                    evidence={
                        "verifyStatus": verification.get("status"),
                        "exportFileCount": exported.get("fileCount"),
                        "importedEventCount": imported_state.get("event_count"),
                        "reAnchorEvent": ((imported_result.get("reAnchor") or {}).get("event") or {}).get("event_id"),
                    },
                )

                resume_marker = "NEXT ACTION: fixture resume packet must preserve verbatim continuity text."
                core.sync_turn(resume_marker, "Fixture resume packet stored.", session_id="trust-gate-resume")
                resume_checkpoint = core.checkpoint(session_id="trust-gate-resume", label="session_end")
                resume = core.rehydrate(session_id="trust-gate-resume", mode="resume")
                resume_packet = resume_checkpoint.get("resumePacket") or {}
                add(
                    "fixture_resume_packet_rehydrate_verbatim",
                    bool(resume_checkpoint.get("ok"))
                    and bool(resume_packet.get("ok"))
                    and bool(resume.get("ok"))
                    and resume.get("mode") == "resume"
                    and resume_marker in str(resume.get("context_block") or ""),
                    "Session resume packets are derived from verified ledger turns and rehydrate verbatim after fail-closed verification.",
                    evidence={
                        "packetFile": resume_packet.get("packetFile"),
                        "rehydrateStatus": resume.get("status"),
                        "mode": resume.get("mode"),
                    },
                )

                backup_dir = root / "encrypted-backups"
                encrypted_backup = core.backup_run(str(backup_dir), keep=10, passphrase="fixture-backup-passphrase")
                encrypted_path = Path(str((encrypted_backup.get("backup") or {}).get("encryptedBundle") or ""))
                manifest_path = Path(str((encrypted_backup.get("backup") or {}).get("manifestFile") or ""))
                restored_backup = TotalRecallCore(TotalRecallConfig(home=root / "backup-restored", enable_lancedb=False, enable_qmd=False))
                restored_result = restored_backup.backup_restore(str(encrypted_path), passphrase="fixture-backup-passphrase", replace=True)
                manifest = (encrypted_backup.get("backup") or {}).get("manifest") or {}
                add(
                    "fixture_encrypted_backup_restore",
                    bool(encrypted_backup.get("ok"))
                    and encrypted_backup.get("encrypted") is True
                    and encrypted_path.exists()
                    and manifest_path.exists()
                    and bool(restored_result.get("ok"))
                    and bool(manifest.get("recipients"))
                    and b"Trust gate" not in encrypted_path.read_bytes(),
                    "Encrypted backup writes a clear manifest plus AES-GCM ciphertext and restores through a non-ledger passphrase recipient.",
                    evidence={
                        "encryptedBundle": str(encrypted_path),
                        "manifestFile": str(manifest_path),
                        "recipientTypes": [item.get("type") for item in manifest.get("recipients") or []],
                        "restoreStatus": restored_result.get("status"),
                    },
                )
                receipt_verify = core.verify(receipts=True)
                add(
                    "fixture_receipt_lineage_verify",
                    bool(receipt_verify.get("ok")) and (receipt_verify.get("receipts") or {}).get("count", 0) > 0,
                    "Checkpoint receipts are device-signed and verify against the current ledger lineage.",
                    evidence={
                        "receiptCount": (receipt_verify.get("receipts") or {}).get("count"),
                        "failures": (receipt_verify.get("receipts") or {}).get("failures"),
                    },
                )

                remote_dir = root / "remote-head"
                pushed = core.backup_push(target=str(remote_dir), passphrase="fixture-remote-passphrase")
                pulled_core = TotalRecallCore(TotalRecallConfig(home=root / "remote-pulled", enable_lancedb=False, enable_qmd=False))
                pulled = pulled_core.backup_pull(target=str(remote_dir), passphrase="fixture-remote-passphrase")
                head_path = remote_dir / "HEAD.json"
                add(
                    "fixture_remote_head_push_pull",
                    bool(pushed.get("ok"))
                    and head_path.exists()
                    and bool((pushed.get("head") or {}).get("signature"))
                    and bool(pulled.get("ok"))
                    and pulled_core.store_id() == core.store_id(),
                    "Local-folder remote push writes a device-signed HEAD and pull restores only after signature and store-id checks.",
                    evidence={
                        "headFile": str(head_path),
                        "pushLatest": (pushed.get("head") or {}).get("latest"),
                        "pullStatus": pulled.get("status"),
                    },
                )

                lease = core.lease_acquire(target=str(remote_dir), ttl_seconds=3600)
                independent_core = TotalRecallCore(TotalRecallConfig(home=root / "remote-second-device", enable_lancedb=False, enable_qmd=False))
                blocked_lease = independent_core.lease_acquire(target=str(remote_dir), ttl_seconds=3600)
                add(
                    "fixture_single_writer_lease_blocks_second_device",
                    bool(lease.get("ok"))
                    and lease.get("lease", {}).get("holder_device_id") == core.device_id()
                    and blocked_lease.get("status") == "LEASE_HELD",
                    "A device-signed remote lease prevents a second device from acquiring the write lease while it is unexpired.",
                    evidence={
                        "holder": (lease.get("lease") or {}).get("holder_device_id"),
                        "secondStatus": blocked_lease.get("status"),
                    },
                )

                fork_base = TotalRecallCore(TotalRecallConfig(home=root / "fork-base", enable_lancedb=False, enable_qmd=False))
                fork_base.ingest(kind="note", text="Fork base memory.", session_id="fork")
                base_bundle = root / "fork-base.tar.gz"
                fork_base.export_bundle(str(base_bundle), include_keys=True)
                fork_local = TotalRecallCore(TotalRecallConfig(home=root / "fork-local", enable_lancedb=False, enable_qmd=False))
                fork_archive = TotalRecallCore(TotalRecallConfig(home=root / "fork-archive", enable_lancedb=False, enable_qmd=False))
                fork_local.import_bundle(str(base_bundle))
                fork_archive.import_bundle(str(base_bundle))
                fork_local.ingest(kind="note", text="Fork local suffix.", session_id="fork")
                fork_archive.ingest(kind="note", text="Fork archive suffix.", session_id="fork")
                archive_bundle = root / "fork-archive.tar.gz"
                fork_archive.export_bundle(str(archive_bundle), include_keys=True)
                forked = fork_local.sync_fork_import(str(archive_bundle))
                fork_suffix = next((item for item in forked.get("quarantined") or [] if item.get("text") == "Fork archive suffix."), {})
                promoted_fork = fork_local.external_promote(fork_suffix.get("external_id", ""), session_id="fork") if fork_suffix else {}
                add(
                    "fixture_fork_import_quarantine_promote",
                    bool(forked.get("ok"))
                    and forked.get("commonPrefixEvents") == 1
                    and forked.get("quarantinedCount") >= 1
                    and bool(promoted_fork.get("ok"))
                    and fork_local.search("Fork archive suffix").get("count", 0) >= 1,
                    "Divergent archive suffixes are quarantined with fork provenance and promoted as new local ledger events, never merged silently.",
                    evidence={
                        "commonPrefixEvents": forked.get("commonPrefixEvents"),
                        "quarantinedCount": forked.get("quarantinedCount"),
                        "promotedEventKind": ((promoted_fork.get("event") or {}).get("kind")),
                    },
                )

                handoff_source = TotalRecallCore(TotalRecallConfig(home=root / "handoff-source", enable_lancedb=False, enable_qmd=False))
                handoff_remote = root / "handoff-remote"
                handoff_marker = "NEXT ACTION: handoff accept must restore this exact resume marker."
                handoff_source.sync_turn("Handoff source turn.", handoff_marker, session_id="handoff-fixture")
                issued = handoff_source.handoff_issue(target=str(handoff_remote), session_id="handoff-fixture", turns=5, passphrase="fixture-handoff-passphrase")
                accepted_core = TotalRecallCore(TotalRecallConfig(home=root / "handoff-accepted", enable_lancedb=False, enable_qmd=False))
                accepted = accepted_core.handoff_accept(str(issued.get("handoffFile") or ""), run_trust_gate=False, passphrase="fixture-handoff-passphrase")
                add(
                    "fixture_handoff_issue_accept_bootstrap",
                    bool(issued.get("ok"))
                    and Path(str(issued.get("handoffFile") or "")).exists()
                    and Path(str(issued.get("bootstrapScript") or "")).exists()
                    and bool(accepted.get("ok"))
                    and handoff_marker in str(accepted.get("resumeBlock") or ""),
                    "Handoff issue writes a resume-packeted encrypted remote backup plus bootstrap artifacts, and accept pulls, verifies, trust-gates, leases, and resumes.",
                    evidence={
                        "handoffFile": issued.get("handoffFile"),
                        "bootstrapScript": issued.get("bootstrapScript"),
                        "acceptStatus": accepted.get("status"),
                        "resumeMode": ((accepted.get("resume") or {}).get("mode")),
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
                    bool(exported.get("ok")) and bool(imported_result.get("ok")) and imported_state.get("event_count") == event_count + 1 and ((imported_result.get("reAnchor") or {}).get("event") or {}).get("kind") == "re_anchor",
                    "Real store can be exported, manifest-verified, imported, reduced, and locally re-anchored without rewriting the restored chain.",
                    evidence={
                        "exportOk": exported.get("ok"),
                        "importOk": imported_result.get("ok"),
                        "eventCount": event_count,
                        "importedEventCount": imported_state.get("event_count"),
                        "lastEventHash": state.get("last_event_hash"),
                        "importedLastEventHash": imported_state.get("last_event_hash"),
                        "reAnchorEvent": ((imported_result.get("reAnchor") or {}).get("event") or {}).get("event_id"),
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
                        member = tar.extractfile("memory/total-recall/plugin.yaml")
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
                        "expectedBundleRoot": "memory/total-recall",
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

    def _encrypted_backup_payload(
        self,
        *,
        plaintext: bytes,
        exported: Dict[str, Any],
        checkpoint_result: Optional[Dict[str, Any]],
        state: Dict[str, Any],
        passphrase: str = "",
    ) -> Dict[str, Any]:
        data_key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, None)
        recipients = self._backup_device_recipients(data_key)
        passphrase_recipient = self._backup_passphrase_recipient(data_key, passphrase=passphrase)
        if passphrase_recipient:
            recipients.append(passphrase_recipient)
        checkpoint = (checkpoint_result or {}).get("checkpoint") or self._latest_checkpoint_summary() or {}
        manifest = {
            "schema": ENCRYPTED_BACKUP_SCHEMA_VERSION,
            "backupId": self._new_id("backup"),
            "created_at": utc_now(),
            "source_device_id": self.device_id(),
            "version": VERSION,
            "bundle_sha256": hashlib.sha256(plaintext).hexdigest(),
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
            "plaintext_bytes": len(plaintext),
            "ciphertext_bytes": len(ciphertext),
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "event_count": state.get("event_count"),
            "last_event_hash": state.get("last_event_hash"),
            "latestCheckpoint": checkpoint,
            "export": exported,
            "recipients": recipients,
            "provider_receipts": [],
        }
        envelope = {
            "schema": ENCRYPTED_BACKUP_SCHEMA_VERSION,
            "manifest": manifest,
            "encryption": {
                "algorithm": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
            },
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        return {"envelope": envelope, "manifest": manifest}

    def _backup_device_recipients(self, data_key: bytes) -> List[Dict[str, Any]]:
        recipients = []
        for device in self.device_list().get("devices") or []:
            if not device.get("approved_at") or device.get("revoked_at"):
                continue
            x_public_hex = str(device.get("x25519_public_key") or "").strip()
            if not x_public_hex:
                continue
            try:
                recipients.append(self._wrap_data_key_for_x25519_device(data_key, device_id=str(device.get("device_id") or ""), public_key_hex=x_public_hex))
            except Exception:
                continue
        return recipients

    def _wrap_data_key_for_x25519_device(self, data_key: bytes, *, device_id: str, public_key_hex: str) -> Dict[str, Any]:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        recipient_public = X25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        ephemeral_private = X25519PrivateKey.generate()
        shared = ephemeral_private.exchange(recipient_public)
        wrap_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"total-recall-backup-device-wrap-v1",
        ).derive(shared)
        nonce = secrets.token_bytes(12)
        wrapped_key = AESGCM(wrap_key).encrypt(nonce, data_key, None)
        ephemeral_public = ephemeral_private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return {
            "type": "device-x25519",
            "device_id": device_id,
            "ephemeral_public_key": ephemeral_public.hex(),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "wrapped_key": base64.b64encode(wrapped_key).decode("ascii"),
        }

    def _backup_passphrase_recipient(self, data_key: bytes, *, passphrase: str = "") -> Optional[Dict[str, Any]]:
        secret = passphrase or os.getenv("TOTAL_RECALL_BACKUP_PASSPHRASE", "")
        if not secret:
            return None
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        iterations = 390_000
        wrap_key = self._portable_clone_key(secret, salt=salt, iterations=iterations)
        wrapped_key = AESGCM(wrap_key).encrypt(nonce, data_key, None)
        return {
            "type": "passphrase-pbkdf2",
            "kdf": "PBKDF2HMAC-SHA256",
            "iterations": iterations,
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "wrapped_key": base64.b64encode(wrapped_key).decode("ascii"),
            "passphrase_env": "TOTAL_RECALL_BACKUP_PASSPHRASE",
        }

    def _decrypt_backup_envelope(self, encrypted_path: Path, *, passphrase: str = "") -> Dict[str, Any]:
        try:
            envelope = json.loads(encrypted_path.read_text(encoding="utf-8"))
            manifest = envelope.get("manifest") or {}
            if envelope.get("schema") != ENCRYPTED_BACKUP_SCHEMA_VERSION or manifest.get("schema") != ENCRYPTED_BACKUP_SCHEMA_VERSION:
                return {"ok": False, "error": "invalid_backup_schema", "bundle": str(encrypted_path)}
            ciphertext = base64.b64decode(envelope.get("ciphertext") or "")
            nonce = base64.b64decode(((envelope.get("encryption") or {}).get("nonce")) or "")
        except Exception as exc:
            return {"ok": False, "error": "backup_envelope_unreadable", "detail": exc.__class__.__name__, "bundle": str(encrypted_path)}
        data_key_result = self._unwrap_backup_data_key(manifest.get("recipients") or [], passphrase=passphrase)
        if not data_key_result.get("ok"):
            return data_key_result
        try:
            plaintext = AESGCM(data_key_result["data_key"]).decrypt(nonce, ciphertext, None)
        except InvalidTag:
            return {"ok": False, "error": "backup_decrypt_failed", "bundle": str(encrypted_path)}
        expected = str(manifest.get("bundle_sha256") or "")
        actual = hashlib.sha256(plaintext).hexdigest()
        if expected and actual != expected:
            return {"ok": False, "error": "backup_plaintext_hash_mismatch", "expected": expected, "actual": actual}
        return {"ok": True, "plaintext": plaintext, "manifest": manifest, "recipient": data_key_result.get("recipient")}

    def _unwrap_backup_data_key(self, recipients: List[Dict[str, Any]], *, passphrase: str = "") -> Dict[str, Any]:
        if self.device_private_key_file.exists() and self.device_x25519_private_key_file.exists():
            current_id = self.device_id()
            for recipient in recipients:
                if recipient.get("type") != "device-x25519" or recipient.get("device_id") != current_id:
                    continue
                try:
                    data_key = self._unwrap_data_key_with_x25519(recipient)
                    return {"ok": True, "data_key": data_key, "recipient": {"type": "device-x25519", "device_id": current_id}}
                except Exception:
                    continue
        secret = passphrase or os.getenv("TOTAL_RECALL_BACKUP_PASSPHRASE", "")
        if secret:
            for recipient in recipients:
                if recipient.get("type") != "passphrase-pbkdf2":
                    continue
                try:
                    salt = base64.b64decode(recipient.get("salt") or "")
                    iterations = int(recipient.get("iterations") or 390_000)
                    nonce = base64.b64decode(recipient.get("nonce") or "")
                    wrapped_key = base64.b64decode(recipient.get("wrapped_key") or "")
                    wrap_key = self._portable_clone_key(secret, salt=salt, iterations=iterations)
                    data_key = AESGCM(wrap_key).decrypt(nonce, wrapped_key, None)
                    return {"ok": True, "data_key": data_key, "recipient": {"type": "passphrase-pbkdf2"}}
                except Exception:
                    continue
        return {"ok": False, "error": "backup_data_key_unavailable", "hint": "Use an approved device key or TOTAL_RECALL_BACKUP_PASSPHRASE."}

    def _unwrap_data_key_with_x25519(self, recipient: Dict[str, Any]) -> bytes:
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        private_key = X25519PrivateKey.from_private_bytes(bytes.fromhex(self._device_x25519_private_key_hex()))
        ephemeral_public = X25519PublicKey.from_public_bytes(bytes.fromhex(str(recipient.get("ephemeral_public_key") or "")))
        shared = private_key.exchange(ephemeral_public)
        wrap_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"total-recall-backup-device-wrap-v1",
        ).derive(shared)
        nonce = base64.b64decode(recipient.get("nonce") or "")
        wrapped_key = base64.b64decode(recipient.get("wrapped_key") or "")
        return AESGCM(wrap_key).decrypt(nonce, wrapped_key, None)

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
        encrypt: bool = True,
        passphrase: str = "",
    ) -> Dict[str, Any]:
        directory = Path(out_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

        checkpoint_result = self.checkpoint(session_id="backup", label="automatic_backup") if checkpoint else None
        state = self.reduce_state(write=True)
        backup_path: Path
        if encrypt:
            backup_path = directory / f"total-recall-backup-{stamp}-{secrets.token_hex(3)}.tar.gz.enc"
            manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
            with tempfile.TemporaryDirectory(prefix="total-recall-backup-plaintext.") as tmpdir:
                plaintext_bundle = Path(tmpdir) / "total-recall-export.tar.gz"
                exported = self.export_bundle(str(plaintext_bundle), include_index=include_index, include_keys=True)
                plaintext = plaintext_bundle.read_bytes()
            encrypted = self._encrypted_backup_payload(
                plaintext=plaintext,
                exported=exported,
                checkpoint_result=checkpoint_result,
                state=state,
                passphrase=passphrase,
            )
            envelope = encrypted["envelope"]
            manifest = encrypted["manifest"]
            backup_path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            manifest["encrypted"] = {
                "path": backup_path.name,
                "sha256": self._file_sha256(backup_path),
                "bytes": backup_path.stat().st_size,
            }
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            exported = {
                "ok": True,
                "schema": ENCRYPTED_BACKUP_SCHEMA_VERSION,
                "encrypted": True,
                "bundle": str(backup_path),
                "encryptedBundle": str(backup_path),
                "manifestFile": str(manifest_path),
                "manifest": manifest,
                "fileCount": exported.get("fileCount"),
                "includeIndex": include_index,
                "includeKeys": True,
            }
        else:
            backup_path = directory / f"total-recall-backup-{stamp}-{secrets.token_hex(3)}.tar.gz"
            exported = self.export_bundle(str(backup_path), include_index=include_index, include_keys=False)
            exported["bundle"] = str(backup_path)
            exported.setdefault("warnings", []).append("plaintext_backup_keys_excluded")
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
            "schema": ENCRYPTED_BACKUP_SCHEMA_VERSION if encrypt else "total-recall-backup-v1",
            "encrypted": encrypt,
            "checkpoint": checkpoint_result,
            "backup": exported,
            "doctor": doctor,
            "verification": verification,
            "retention": {"keep": keep, "keepDays": keep_days, "pruned": pruned},
            "backupStatus": self.backup_status(str(directory)),
        }
        payload["report"] = self._write_report("backup", "latest", payload)
        return payload

    def backup_restore(
        self,
        bundle: str | Path,
        *,
        passphrase: str = "",
        replace: bool = False,
    ) -> Dict[str, Any]:
        encrypted_path = Path(bundle).expanduser()
        if not encrypted_path.exists():
            return {"ok": False, "error": "backup_bundle_not_found", "bundle": str(encrypted_path)}
        if not encrypted_path.name.endswith(".enc"):
            imported = self.import_bundle(str(encrypted_path), replace=replace)
            return {
                "ok": bool(imported.get("ok")),
                "schema": "total-recall-backup-restore-v1",
                "encrypted": False,
                "bundle": str(encrypted_path),
                "import": imported,
                "verification": imported.get("verification"),
            }
        decrypted = self._decrypt_backup_envelope(encrypted_path, passphrase=passphrase)
        if not decrypted.get("ok"):
            return {"ok": False, "schema": ENCRYPTED_BACKUP_SCHEMA_VERSION, "status": "FAIL_CLOSED", **decrypted}
        with tempfile.TemporaryDirectory(prefix="total-recall-backup-restore.") as tmpdir:
            plaintext_bundle = Path(tmpdir) / "total-recall-export.tar.gz"
            plaintext_bundle.write_bytes(decrypted["plaintext"])
            imported = self.import_bundle(str(plaintext_bundle), replace=replace)
        verification = imported.get("verification") or self.verify()
        return {
            "ok": bool(imported.get("ok")) and bool(verification.get("ok")),
            "schema": ENCRYPTED_BACKUP_SCHEMA_VERSION,
            "status": "PASS" if imported.get("ok") and verification.get("ok") else "FAIL_CLOSED",
            "encrypted": True,
            "bundle": str(encrypted_path),
            "manifest": decrypted.get("manifest"),
            "recipient": decrypted.get("recipient"),
            "import": imported,
            "verification": verification,
        }

    def backup_push(self, *, target: str, passphrase: str = "") -> Dict[str, Any]:
        remote = self._resolve_remote_target(target)
        if not remote.get("ok"):
            return remote
        with tempfile.TemporaryDirectory(prefix="total-recall-push.") as tmpdir:
            backup = self.backup_run(tmpdir, keep=10, encrypt=True, passphrase=passphrase)
            if not backup.get("ok"):
                return {"ok": False, "error": "backup_failed", "backup": backup}
            backup_info = backup.get("backup") or {}
            bundle_path = Path(str(backup_info.get("encryptedBundle") or ""))
            manifest_path = Path(str(backup_info.get("manifestFile") or ""))
            uploaded = self._remote_upload_files(remote, [bundle_path, manifest_path])
            if not uploaded.get("ok"):
                return uploaded
            manifest = backup_info.get("manifest") or self._read_json(manifest_path)
            latest = {
                "bundle": bundle_path.name,
                "manifest": manifest_path.name,
                "bundle_sha256": self._file_sha256(bundle_path),
                "checkpoint_id": manifest.get("checkpoint_id"),
                "event_count": manifest.get("event_count"),
                "last_event_hash": manifest.get("last_event_hash"),
                "created_at": manifest.get("created_at"),
                "device_id": self.device_id(),
            }
            existing_head = self._remote_read_head(remote)
            receipts = self._receipts_tail()
            head = {
                "schema": "total-recall-remote-head-v1",
                "updated_at": utc_now(),
                "store_id": self.store_id(),
                "latest": latest,
                "lease": (existing_head.get("head") or {}).get("lease") if existing_head.get("ok") else None,
                "receipts": receipts,
            }
            head["signature"] = self._device_sign_json({"latest": latest, "receipts": receipts})
            written = self._remote_write_head(remote, head)
            return {
                "ok": bool(written.get("ok")),
                "schema": "total-recall-remote-head-v1",
                "target": remote,
                "backup": backup,
                "upload": uploaded,
                "head": head,
                "headFile": written.get("headFile"),
            }

    def backup_pull(self, *, target: str, passphrase: str = "", replace: bool = True) -> Dict[str, Any]:
        remote = self._resolve_remote_target(target)
        if not remote.get("ok"):
            return remote
        head_result = self._remote_read_head(remote)
        if not head_result.get("ok"):
            return head_result
        head = head_result["head"]
        verified = self._verify_remote_head(head)
        if not verified.get("ok"):
            return {"ok": False, "status": "FAIL_CLOSED", "error": "remote_head_signature_invalid", "head": head}
        store_check = self._remote_store_id_check(str(head.get("store_id") or ""))
        if not store_check.get("ok"):
            return store_check
        relation = self._remote_relation(head)
        if relation["relation"] == "in_sync":
            return {"ok": True, "status": "IN_SYNC", "relation": relation, "head": head}
        if relation["relation"] == "local_ahead":
            return {"ok": False, "status": "LOCAL_AHEAD", "relation": relation, "message": "Local store is ahead; push instead of pulling."}
        if relation["relation"] == "diverged":
            return {"ok": False, "status": "DIVERGED", "relation": relation, "message": "Local and remote diverged; use sync fork-import."}
        fetched = self._remote_fetch_latest_bundle(remote, head)
        if not fetched.get("ok"):
            return fetched
        restored = self.backup_restore(fetched["bundle"], passphrase=passphrase, replace=replace)
        if restored.get("ok"):
            self._set_store_id(str(head.get("store_id") or ""))
        return {
            "ok": bool(restored.get("ok")),
            "status": "PASS" if restored.get("ok") else "FAIL_CLOSED",
            "relation": relation,
            "head": head,
            "fetch": fetched,
            "restore": restored,
        }

    def sync_check(self, *, target: str) -> Dict[str, Any]:
        remote = self._resolve_remote_target(target)
        if not remote.get("ok"):
            return remote
        head_result = self._remote_read_head(remote)
        if not head_result.get("ok"):
            return head_result
        head = head_result["head"]
        signature = self._verify_remote_head(head)
        relation = self._remote_relation(head) if signature.get("ok") else {"relation": "unknown", "message": "Remote HEAD signature invalid."}
        return {"ok": bool(signature.get("ok")), "schema": "total-recall-sync-check-v1", "target": remote, "head": head, "signature": signature, "relation": relation}

    def sync_fork_import(self, source: str, *, passphrase: str = "") -> Dict[str, Any]:
        archive = self._archive_events_from_source(source, passphrase=passphrase)
        if not archive.get("ok"):
            return archive
        archive_events = archive.get("events") or []
        local_events = self._read_events(verify_chain=True)
        prefix_len = 0
        for local_event, archive_event in zip(local_events, archive_events):
            if local_event.get("hash") != archive_event.get("hash"):
                break
            prefix_len += 1
        fork_base_hash = local_events[prefix_len - 1].get("hash") if prefix_len > 0 and local_events else None
        suffix = archive_events[prefix_len:]
        quarantined = []
        for event in suffix:
            external_id = self._new_id("fork")
            item = {
                "schema": "total-recall-external-v1",
                "external_id": external_id,
                "created_at": utc_now(),
                "source": f"fork-import:{archive.get('source')}",
                "source_kind": "ledger_fork",
                "text": event.get("text", ""),
                "status": "quarantine",
                "metadata": {
                    "fork_import": {
                        "fork_base_hash": fork_base_hash,
                        "archive_bundle": archive.get("source"),
                        "origin_device": ((event.get("origin") or {}).get("device_id")),
                        "original_event_hash": event.get("hash"),
                        "original_event_id": event.get("event_id"),
                        "original_event": event,
                    }
                },
            }
            path = self.home / "external-memory" / "quarantine" / f"{external_id}.json"
            self._write_json(path, item)
            quarantined.append({**item, "externalFile": str(path)})
        return {
            "ok": True,
            "schema": "total-recall-fork-import-v1",
            "source": archive.get("source"),
            "localEventCount": len(local_events),
            "archiveEventCount": len(archive_events),
            "commonPrefixEvents": prefix_len,
            "forkBaseHash": fork_base_hash,
            "quarantinedCount": len(quarantined),
            "quarantined": quarantined,
        }

    def lease_status(self, *, target: str) -> Dict[str, Any]:
        remote = self._resolve_remote_target(target)
        if not remote.get("ok"):
            return remote
        head_result = self._remote_read_head(remote)
        if not head_result.get("ok"):
            return head_result
        head = head_result["head"]
        lease = head.get("lease")
        return {
            "ok": True,
            "schema": "total-recall-lease-status-v1",
            "target": remote,
            "lease": lease,
            "active": self._lease_active(lease),
            "heldBySelf": bool(lease and lease.get("holder_device_id") == self.device_id()),
            "head": head,
        }

    def lease_acquire(self, *, target: str, ttl_seconds: int = 3600) -> Dict[str, Any]:
        remote, head_result = self._remote_head_for_lease(target)
        if not head_result.get("ok"):
            return head_result
        head = head_result["head"]
        lease = head.get("lease")
        if self._lease_active(lease) and (lease or {}).get("holder_device_id") != self.device_id():
            return {"ok": False, "status": "LEASE_HELD", "lease": lease, "message": "Another device holds an unexpired lease."}
        new_lease = self._new_lease(ttl_seconds=ttl_seconds)
        head["lease"] = new_lease
        head["updated_at"] = utc_now()
        written = self._remote_write_head(remote, head)
        return {"ok": bool(written.get("ok")), "schema": "total-recall-lease-v1", "status": "ACQUIRED", "lease": new_lease, "headFile": written.get("headFile")}

    def lease_release(self, *, target: str) -> Dict[str, Any]:
        remote, head_result = self._remote_head_for_lease(target)
        if not head_result.get("ok"):
            return head_result
        head = head_result["head"]
        lease = head.get("lease")
        if lease and self._lease_active(lease) and lease.get("holder_device_id") != self.device_id():
            return {"ok": False, "status": "LEASE_HELD_BY_OTHER", "lease": lease}
        head["lease"] = None
        head["updated_at"] = utc_now()
        written = self._remote_write_head(remote, head)
        return {"ok": bool(written.get("ok")), "schema": "total-recall-lease-v1", "status": "RELEASED", "headFile": written.get("headFile")}

    def lease_steal(self, *, target: str, ttl_seconds: int = 3600, force: bool = False) -> Dict[str, Any]:
        if not force:
            return {"ok": False, "error": "force_required", "hint": "Pass --force to steal a lease and record an incident."}
        remote, head_result = self._remote_head_for_lease(target)
        if not head_result.get("ok"):
            return head_result
        old_lease = (head_result["head"] or {}).get("lease")
        incident = self.create_incident(
            title="Remote lease stolen",
            severity="DEGRADED",
            summary=f"Lease on {target} was force-stolen by {self.device_id()}.",
            metadata={"target": target, "oldLease": old_lease, "device_id": self.device_id()},
        )
        event = self.ingest(
            kind="lease_steal",
            text=f"Lease stolen for target {target}. Previous lease: {canonical_json(old_lease or {})}",
            session_id="lease",
            source="lease.steal",
            metadata={"target": target, "old_lease": old_lease, "incident_id": (incident.get("incident") or {}).get("incident_id")},
        )
        head = head_result["head"]
        new_lease = self._new_lease(ttl_seconds=ttl_seconds)
        head["lease"] = new_lease
        head["updated_at"] = utc_now()
        written = self._remote_write_head(remote, head)
        return {
            "ok": bool(written.get("ok")),
            "schema": "total-recall-lease-v1",
            "status": "STOLEN",
            "lease": new_lease,
            "incident": incident.get("incident"),
            "event": event.get("event"),
            "headFile": written.get("headFile"),
        }

    def _remote_head_for_lease(self, target: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        remote = self._resolve_remote_target(target)
        if not remote.get("ok"):
            return remote, remote
        head_result = self._remote_read_head(remote)
        if not head_result.get("ok"):
            return remote, head_result
        verified = self._verify_remote_head(head_result["head"])
        if not verified.get("ok"):
            return remote, {"ok": False, "status": "FAIL_CLOSED", "error": "remote_head_signature_invalid"}
        return remote, head_result

    def _new_lease(self, *, ttl_seconds: int) -> Dict[str, Any]:
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        ttl = max(1, int(ttl_seconds or 3600))
        expires_dt = now_dt.timestamp() + ttl
        expires_at = datetime.fromtimestamp(expires_dt, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        device = self._register_self_device()
        base = {
            "holder_device_id": self.device_id(),
            "holder_label": device.get("label") or self.device_id(),
            "acquired_at": now_dt.isoformat().replace("+00:00", "Z"),
            "ttl_seconds": ttl,
            "expires_at": expires_at,
        }
        return {**base, "signature": self._device_sign_json(base)}

    def _lease_active(self, lease: Any) -> bool:
        if not isinstance(lease, dict) or not lease.get("expires_at"):
            return False
        if not self._verify_lease_signature(lease):
            return False
        try:
            expires = datetime.strptime(str(lease.get("expires_at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            return False
        return expires > datetime.now(timezone.utc)

    def _verify_lease_signature(self, lease: Dict[str, Any]) -> bool:
        signature = lease.get("signature") or {}
        base = {k: v for k, v in lease.items() if k != "signature"}
        return self._verify_device_signature(base, signature)

    def _resolve_remote_target(self, target: str) -> Dict[str, Any]:
        raw = str(target or "").strip()
        if not raw:
            return {"ok": False, "error": "target_required"}
        registry_path = self.home / "remote" / "targets.json"
        if registry_path.exists():
            try:
                registry = self._read_json(registry_path)
                configured = (registry.get("targets") or {}).get(raw)
                if configured:
                    configured = dict(configured)
                    configured.setdefault("name", raw)
                    configured["ok"] = True
                    return configured
            except Exception:
                pass
        if raw.startswith("hf:"):
            return {"ok": True, "name": raw, "type": "huggingface", "repo_id": raw.removeprefix("hf:")}
        return {"ok": True, "name": raw, "type": "local-folder", "path": str(Path(raw).expanduser())}

    def _remote_upload_files(self, remote: Dict[str, Any], files: List[Path]) -> Dict[str, Any]:
        if remote.get("type") == "local-folder":
            root = Path(str(remote.get("path") or "")).expanduser()
            root.mkdir(parents=True, exist_ok=True)
            copied = []
            for path in files:
                dest = root / path.name
                shutil.copy2(path, dest)
                copied.append(str(dest))
            return {"ok": True, "provider": "local-folder", "files": copied}
        if remote.get("type") == "huggingface":
            repo_id = str(remote.get("repo_id") or "")
            results = []
            for path in files:
                result = self._remote_hf_upload_file(repo_id=repo_id, path=path)
                results.append(result)
                if not result.get("ok"):
                    return {"ok": False, "provider": "huggingface", "repoId": repo_id, "results": results}
            return {"ok": True, "provider": "huggingface", "repoId": repo_id, "results": results}
        return {"ok": False, "error": "remote_type_unsupported", "type": remote.get("type")}

    def _remote_read_head(self, remote: Dict[str, Any]) -> Dict[str, Any]:
        if remote.get("type") == "local-folder":
            path = Path(str(remote.get("path") or "")).expanduser() / "HEAD.json"
            if not path.exists():
                return {"ok": False, "error": "remote_head_not_found", "headFile": str(path)}
            return {"ok": True, "head": self._read_json(path), "headFile": str(path)}
        if remote.get("type") == "huggingface":
            downloaded = self._portable_clone_hf_download(repo_id=str(remote.get("repo_id") or ""), filename="HEAD.json")
            if not downloaded.get("ok"):
                return downloaded
            return {"ok": True, "head": self._read_json(Path(downloaded["path"])), "headFile": downloaded["path"]}
        return {"ok": False, "error": "remote_type_unsupported", "type": remote.get("type")}

    def _remote_write_head(self, remote: Dict[str, Any], head: Dict[str, Any]) -> Dict[str, Any]:
        if remote.get("type") == "local-folder":
            path = Path(str(remote.get("path") or "")).expanduser() / "HEAD.json"
            self._write_json(path, head)
            return {"ok": True, "headFile": str(path)}
        if remote.get("type") == "huggingface":
            with tempfile.TemporaryDirectory(prefix="total-recall-head-upload.") as tmpdir:
                path = Path(tmpdir) / "HEAD.json"
                path.write_text(json.dumps(head, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                return self._remote_hf_upload_file(repo_id=str(remote.get("repo_id") or ""), path=path)
        return {"ok": False, "error": "remote_type_unsupported", "type": remote.get("type")}

    def _remote_fetch_latest_bundle(self, remote: Dict[str, Any], head: Dict[str, Any]) -> Dict[str, Any]:
        latest = head.get("latest") or {}
        bundle = str(latest.get("bundle") or "")
        manifest = str(latest.get("manifest") or "")
        if not bundle:
            return {"ok": False, "error": "remote_latest_bundle_missing"}
        if remote.get("type") == "local-folder":
            root = Path(str(remote.get("path") or "")).expanduser()
            bundle_path = root / bundle
            manifest_path = root / manifest if manifest else None
            if not bundle_path.exists():
                return {"ok": False, "error": "remote_bundle_not_found", "bundle": str(bundle_path)}
            return {"ok": True, "bundle": str(bundle_path), "manifest": str(manifest_path) if manifest_path else ""}
        if remote.get("type") == "huggingface":
            bundle_result = self._portable_clone_hf_download(repo_id=str(remote.get("repo_id") or ""), filename=bundle)
            if not bundle_result.get("ok"):
                return bundle_result
            manifest_result = self._portable_clone_hf_download(repo_id=str(remote.get("repo_id") or ""), filename=manifest) if manifest else {"ok": True}
            return {"ok": bool(manifest_result.get("ok")), "bundle": bundle_result.get("path"), "manifest": manifest_result.get("path")}
        return {"ok": False, "error": "remote_type_unsupported", "type": remote.get("type")}

    def _verify_remote_head(self, head: Dict[str, Any]) -> Dict[str, Any]:
        if head.get("schema") != "total-recall-remote-head-v1":
            return {"ok": False, "error": "invalid_remote_head_schema"}
        signed_payload = {"latest": head.get("latest") or {}, "receipts": head.get("receipts") or []}
        ok = self._verify_device_signature(signed_payload, head.get("signature") or {})
        return {"ok": ok, "device_id": ((head.get("signature") or {}).get("device_id"))}

    def _remote_store_id_check(self, remote_store_id: str) -> Dict[str, Any]:
        state = self.reduce_state(write=False)
        local_events = int(state.get("event_count") or 0)
        local_path = self.home / "state" / "store_id"
        local_store_id = local_path.read_text(encoding="utf-8").strip() if local_path.exists() else ""
        if local_store_id and remote_store_id and local_store_id != remote_store_id and local_events > 0:
            return {"ok": False, "error": "store_id_mismatch", "localStoreId": local_store_id, "remoteStoreId": remote_store_id}
        return {"ok": True, "localStoreId": local_store_id, "remoteStoreId": remote_store_id}

    def _remote_relation(self, head: Dict[str, Any]) -> Dict[str, Any]:
        state = self.reduce_state(write=False)
        latest = head.get("latest") or {}
        local_count = int(state.get("event_count") or 0)
        remote_count = int(latest.get("event_count") or 0)
        local_hash = state.get("last_event_hash")
        remote_hash = latest.get("last_event_hash")
        if local_count == remote_count and local_hash == remote_hash:
            relation = "in_sync"
            message = "Local store and remote HEAD pin the same ledger point."
        elif remote_count > local_count:
            relation = "archive_ahead"
            message = f"Remote archive is ahead by {remote_count - local_count} event(s)."
        elif local_count > remote_count:
            relation = "local_ahead"
            message = f"Local store is ahead by {local_count - remote_count} event(s)."
        else:
            relation = "diverged"
            message = "Local and remote event counts match but ledger hashes differ."
        return {
            "relation": relation,
            "message": message,
            "local": {"eventCount": local_count, "lastEventHash": local_hash},
            "remote": {"eventCount": remote_count, "lastEventHash": remote_hash},
        }

    def _archive_events_from_source(self, source: str, *, passphrase: str = "") -> Dict[str, Any]:
        raw = str(source or "").strip()
        path = Path(raw).expanduser()
        bundle_path: Optional[Path] = None
        if path.exists() and path.is_dir():
            remote = {"ok": True, "type": "local-folder", "path": str(path), "name": raw}
            head_result = self._remote_read_head(remote)
            if not head_result.get("ok"):
                return head_result
            verified = self._verify_remote_head(head_result["head"])
            if not verified.get("ok"):
                return {"ok": False, "error": "remote_head_signature_invalid"}
            fetched = self._remote_fetch_latest_bundle(remote, head_result["head"])
            if not fetched.get("ok"):
                return fetched
            bundle_path = Path(str(fetched.get("bundle") or ""))
        elif path.exists() and path.is_file():
            bundle_path = path
        else:
            remote = self._resolve_remote_target(raw)
            if not remote.get("ok"):
                return {"ok": False, "error": "source_not_found", "source": raw}
            head_result = self._remote_read_head(remote)
            if not head_result.get("ok"):
                return head_result
            verified = self._verify_remote_head(head_result["head"])
            if not verified.get("ok"):
                return {"ok": False, "error": "remote_head_signature_invalid"}
            fetched = self._remote_fetch_latest_bundle(remote, head_result["head"])
            if not fetched.get("ok"):
                return fetched
            bundle_path = Path(str(fetched.get("bundle") or ""))
        if not bundle_path or not bundle_path.exists():
            return {"ok": False, "error": "bundle_not_found", "source": raw}
        if bundle_path.name.endswith(".enc"):
            decrypted = self._decrypt_backup_envelope(bundle_path, passphrase=passphrase)
            if not decrypted.get("ok"):
                return decrypted
            with tempfile.TemporaryDirectory(prefix="total-recall-fork-import.") as tmpdir:
                plain = Path(tmpdir) / "archive.tar.gz"
                plain.write_bytes(decrypted["plaintext"])
                events = self._events_from_export_tarball(plain)
        else:
            events = self._events_from_export_tarball(bundle_path)
        if not events.get("ok"):
            return events
        return {"ok": True, "source": str(bundle_path), "events": events.get("events") or []}

    def _events_from_export_tarball(self, bundle: Path) -> Dict[str, Any]:
        try:
            with tarfile.open(bundle, "r:gz") as tar:
                member = tar.extractfile("ledger/events.jsonl")
                if member is None:
                    return {"ok": False, "error": "ledger_not_found_in_bundle", "bundle": str(bundle)}
                lines = member.read().decode("utf-8").splitlines()
        except Exception as exc:
            return {"ok": False, "error": "bundle_unreadable", "bundle": str(bundle), "detail": str(exc)}
        events: List[Dict[str, Any]] = []
        prev: Optional[str] = None
        for line_no, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            event = json.loads(line)
            event_hash = event.get("hash")
            base = {k: v for k, v in event.items() if k != "hash"}
            if event_hash != sha256_json(base):
                return {"ok": False, "error": "archive_hash_mismatch", "line": line_no}
            if event.get("prev_hash") != prev:
                return {"ok": False, "error": "archive_prev_hash_mismatch", "line": line_no}
            prev = event_hash
            events.append(event)
        return {"ok": True, "events": events, "eventCount": len(events)}

    def _receipts_tail(self) -> List[Dict[str, Any]]:
        path = self.home / "anchors" / "receipts.jsonl"
        if not path.exists():
            return []
        receipts = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                receipts.append(json.loads(line))
            except Exception:
                continue
        return receipts[-100:]

    def _verify_receipts_against_events(self, state: Dict[str, Any]) -> Dict[str, Any]:
        receipts = self._receipts_tail()
        if not receipts:
            return {"ok": True, "count": 0, "status": "NO_RECEIPTS"}
        events = self._read_events(verify_chain=True)
        hashes = {str(event.get("hash") or "") for event in events if event.get("hash")}
        failures = []
        for receipt in receipts:
            signature = receipt.get("signature") or {}
            base = {k: v for k, v in receipt.items() if k != "signature"}
            if not self._verify_device_signature(base, signature):
                failures.append({"checkpoint_id": receipt.get("checkpoint_id"), "error": "signature_mismatch"})
                continue
            event_count = int(receipt.get("event_count") or 0)
            last_hash = str(receipt.get("last_event_hash") or "")
            if event_count > int(state.get("event_count") or 0):
                failures.append({"checkpoint_id": receipt.get("checkpoint_id"), "error": "event_count_ahead"})
            if last_hash and last_hash not in hashes:
                failures.append({"checkpoint_id": receipt.get("checkpoint_id"), "error": "last_event_hash_not_in_chain"})
        return {"ok": not failures, "count": len(receipts), "failures": failures}

    def _remote_hf_upload_file(self, *, repo_id: str, path: Path) -> Dict[str, Any]:
        if not repo_id:
            return {"ok": False, "error": "repo_id_required", "provider": "huggingface"}
        hf_bin = shutil.which("hf") or shutil.which("huggingface-cli")
        if not hf_bin:
            return {"ok": False, "error": "hf_cli_not_found", "provider": "huggingface", "file": str(path)}
        command = [hf_bin, "upload", repo_id, str(path), path.name, "--repo-type", "dataset"]
        run = subprocess.run(command, text=True, capture_output=True, timeout=300)
        return {
            "ok": run.returncode == 0,
            "provider": "huggingface",
            "repoId": repo_id,
            "file": path.name,
            "returncode": run.returncode,
            "stdout": _redact_secret_text(run.stdout[-1000:]),
            "stderr": _redact_secret_text(run.stderr[-1000:]),
        }

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
            "continuation",
            "handoff",
            "index",
            "keys",
            "devices",
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
            "portable-clones",
            "loops",
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

    def _append_checkpoint_receipt(self, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        base = {
            "schema": "total-recall-checkpoint-receipt-v1",
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "state_hash": checkpoint.get("state_hash"),
            "event_count": checkpoint.get("event_count"),
            "last_event_hash": checkpoint.get("last_event_hash"),
            "created_at": utc_now(),
            "device_id": self.device_id(),
        }
        receipt = {**base, "signature": self._device_sign_json(base)}
        path = self.home / "anchors" / "receipts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(receipt) + "\n")
        return receipt

    def _append_re_anchor_event(
        self,
        *,
        restored_checkpoint_id: str,
        restored_last_event_hash: str,
        source_bundle_sha256: str,
        source: str,
    ) -> Dict[str, Any]:
        base = {
            "schema": "total-recall-re-anchor-v1",
            "restored_checkpoint_id": restored_checkpoint_id,
            "restored_last_event_hash": restored_last_event_hash,
            "source_bundle_sha256": source_bundle_sha256,
            "device_id": self.device_id(),
            "created_at": utc_now(),
        }
        metadata = {**base, "signature": self._device_sign_json(base)}
        return self.ingest(
            kind="re_anchor",
            text=f"Re-anchor after restore/import from {source}. Restored checkpoint {restored_checkpoint_id} at {restored_last_event_hash}.",
            session_id="re-anchor",
            source=source,
            metadata=metadata,
        )

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

    def _device_private_key_hex(self) -> str:
        if not self.device_private_key_file.exists():
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
            self.device_private_key_file.parent.mkdir(parents=True, exist_ok=True)
            self.device_private_key_file.write_text(private_bytes.hex() + "\n", encoding="utf-8")
            self.device_public_key_file.write_text(public_bytes.hex() + "\n", encoding="utf-8")
            try:
                self.device_private_key_file.chmod(0o600)
                self.device_public_key_file.chmod(0o644)
            except Exception:
                pass
        return self.device_private_key_file.read_text(encoding="utf-8").strip()

    def _device_public_key_hex(self) -> str:
        if not self.device_public_key_file.exists():
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(self._device_private_key_hex()))
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            self.device_public_key_file.write_text(public_bytes.hex() + "\n", encoding="utf-8")
        return self.device_public_key_file.read_text(encoding="utf-8").strip()

    def _device_x25519_private_key_hex(self) -> str:
        if not self.device_x25519_private_key_file.exists():
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

            private_key = X25519PrivateKey.generate()
            private_bytes = private_key.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            self.device_x25519_private_key_file.parent.mkdir(parents=True, exist_ok=True)
            self.device_x25519_private_key_file.write_text(private_bytes.hex() + "\n", encoding="utf-8")
            self.device_x25519_public_key_file.write_text(public_bytes.hex() + "\n", encoding="utf-8")
            try:
                self.device_x25519_private_key_file.chmod(0o600)
                self.device_x25519_public_key_file.chmod(0o644)
            except Exception:
                pass
        return self.device_x25519_private_key_file.read_text(encoding="utf-8").strip()

    def _device_x25519_public_key_hex(self) -> str:
        if not self.device_x25519_public_key_file.exists():
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

            private_key = X25519PrivateKey.from_private_bytes(bytes.fromhex(self._device_x25519_private_key_hex()))
            public_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            self.device_x25519_public_key_file.write_text(public_bytes.hex() + "\n", encoding="utf-8")
        return self.device_x25519_public_key_file.read_text(encoding="utf-8").strip()

    def device_id(self) -> str:
        return hashlib.sha256(bytes.fromhex(self._device_public_key_hex())).hexdigest()[:16]

    def store_id(self) -> str:
        path = self.home / "state" / "store_id"
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = str(uuid.uuid4())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")
        return value

    def _set_store_id(self, store_id: str) -> None:
        value = str(store_id or "").strip()
        if not value:
            return
        path = self.home / "state" / "store_id"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")

    def device_init(self, *, label: str = "") -> Dict[str, Any]:
        return {"ok": True, "device": self._register_self_device(label=label), "schema": "total-recall-device-registry-v1"}

    def device_list(self) -> Dict[str, Any]:
        self._register_self_device()
        devices = []
        for path in sorted((self.home / "devices").glob("device_*.json")):
            try:
                item = self._read_json(path)
            except Exception:
                continue
            item["deviceFile"] = str(path)
            devices.append(item)
        return {"ok": True, "schema": "total-recall-device-registry-v1", "devices": devices, "count": len(devices)}

    def device_approve(self, device_id: str = "", *, public_key: str = "", x25519_public_key: str = "", label: str = "") -> Dict[str, Any]:
        public_key = str(public_key or "").strip()
        x25519_public_key = str(x25519_public_key or "").strip()
        safe_device_id = _safe_id(device_id or "")
        if public_key:
            try:
                public_key_bytes = bytes.fromhex(public_key)
            except ValueError:
                return {"ok": False, "error": "invalid_public_key_hex"}
            calculated = hashlib.sha256(public_key_bytes).hexdigest()[:16]
            if safe_device_id and safe_device_id != calculated:
                return {"ok": False, "error": "device_id_public_key_mismatch", "expected": calculated, "device_id": safe_device_id}
            safe_device_id = calculated
        if not safe_device_id:
            return {"ok": False, "error": "device_id_required"}
        path = self.home / "devices" / f"device_{safe_device_id}.json"
        now = utc_now()
        device = self._read_json(path) if path.exists() else {
            "schema": "total-recall-device-v1",
            "device_id": safe_device_id,
            "label": label or safe_device_id,
            "public_key": public_key,
            "x25519_public_key": x25519_public_key,
            "created_at": now,
            "approved_at": None,
            "revoked_at": None,
            "last_seen_at": None,
        }
        if public_key:
            device["public_key"] = public_key
        if x25519_public_key:
            try:
                bytes.fromhex(x25519_public_key)
            except ValueError:
                return {"ok": False, "error": "invalid_x25519_public_key_hex"}
            device["x25519_public_key"] = x25519_public_key
        if label:
            device["label"] = label
        device["approved_at"] = device.get("approved_at") or now
        device["revoked_at"] = None
        self._write_json(path, device)
        return {"ok": True, "schema": "total-recall-device-registry-v1", "device": device, "deviceFile": str(path)}

    def device_revoke(self, device_id: str) -> Dict[str, Any]:
        safe_device_id = _safe_id(device_id or "")
        if not safe_device_id:
            return {"ok": False, "error": "device_id_required"}
        path = self.home / "devices" / f"device_{safe_device_id}.json"
        if not path.exists():
            return {"ok": False, "error": "device_not_found", "device_id": safe_device_id}
        device = self._read_json(path)
        device["revoked_at"] = utc_now()
        self._write_json(path, device)
        return {"ok": True, "schema": "total-recall-device-registry-v1", "device": device, "deviceFile": str(path)}

    def _register_self_device(self, *, label: str = "") -> Dict[str, Any]:
        now = utc_now()
        public_key = self._device_public_key_hex()
        x25519_public_key = self._device_x25519_public_key_hex()
        device_id = hashlib.sha256(bytes.fromhex(public_key)).hexdigest()[:16]
        path = self.home / "devices" / f"device_{device_id}.json"
        device = self._read_json(path) if path.exists() else {
            "schema": "total-recall-device-v1",
            "device_id": device_id,
            "label": label or platform.node() or "local-device",
            "public_key": public_key,
            "x25519_public_key": x25519_public_key,
            "created_at": now,
            "approved_at": now,
            "revoked_at": None,
            "last_seen_at": None,
        }
        if label:
            device["label"] = label
        device["public_key"] = public_key
        device["x25519_public_key"] = x25519_public_key
        device["approved_at"] = device.get("approved_at") or now
        device["last_seen_at"] = now
        self._write_json(path, device)
        return device

    def _event_origin(self, *, source: str = "") -> Dict[str, Any]:
        device = self._register_self_device()
        source_text = str(source or "")
        harness = "hermes" if source_text.startswith("hermes.") or "hermes" in source_text.lower() else "cli"
        return {
            "device_id": device.get("device_id"),
            "agent": os.getenv("TOTAL_RECALL_AGENT", ""),
            "harness": harness,
            "host": platform.node(),
        }

    def _device_sign_json(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(self._device_private_key_hex()))
        body = canonical_json(payload).encode("utf-8")
        return {
            "algorithm": "ed25519-device-v1",
            "device_id": self.device_id(),
            "public_key": self._device_public_key_hex(),
            "signature": private_key.sign(body).hex(),
        }

    @staticmethod
    def _verify_device_signature(payload: Dict[str, Any], signature: Dict[str, Any]) -> bool:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

            if signature.get("algorithm") != "ed25519-device-v1":
                return False
            public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(str(signature.get("public_key") or "")))
            public_key.verify(bytes.fromhex(str(signature.get("signature") or "")), canonical_json(payload).encode("utf-8"))
            return True
        except Exception:
            return False

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
            [
                path
                for path in directory.glob("total-recall-backup-*")
                if path.is_file()
                and (path.name.endswith(".tar.gz") or path.name.endswith(".tar.gz.enc"))
                and not path.name.endswith(".manifest.json")
            ],
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
        if bundle.name.endswith(".enc"):
            try:
                sidecar = bundle.with_suffix(bundle.suffix + ".manifest.json")
                if sidecar.exists():
                    manifest = self._read_json(sidecar)
                else:
                    envelope = json.loads(bundle.read_text(encoding="utf-8"))
                    manifest = envelope.get("manifest") or {}
            except Exception as exc:
                return {"ok": False, "error": "encrypted_manifest_unreadable", "bundle": str(bundle), "detail": str(exc)}
            return {
                "ok": True,
                "bundle": str(bundle),
                "encrypted": True,
                "schema": manifest.get("schema"),
                "bytes": bundle.stat().st_size,
                "modified": datetime.fromtimestamp(bundle.stat().st_mtime, tz=timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
                "manifest": {
                    "schema": manifest.get("schema"),
                    "version": manifest.get("version"),
                    "created_at": manifest.get("created_at"),
                    "recipientCount": len(manifest.get("recipients") or []),
                    "ciphertextSha256": manifest.get("ciphertext_sha256"),
                    "bundleSha256": manifest.get("bundle_sha256"),
                },
                "latestCheckpoint": manifest.get("latestCheckpoint") or {
                    "checkpoint_id": manifest.get("checkpoint_id"),
                    "event_count": manifest.get("event_count"),
                    "last_event_hash": manifest.get("last_event_hash"),
                },
            }
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
        latest_checkpoint = max(
            checkpoints,
            key=lambda item: (str(item.get("created_at") or ""), int(item.get("event_count") or 0)),
        ) if checkpoints else None
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

    def _portable_clone_key(self, passphrase: str, *, salt: bytes, iterations: int) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=iterations,
        )
        return kdf.derive(passphrase.encode("utf-8"))

    def _portable_clone_hf_upload(self, *, repo_id: str, encrypted_path: Path, manifest_path: Path) -> Dict[str, Any]:
        if not repo_id:
            return {"ok": False, "error": "repo_id_required", "provider": "huggingface"}
        hf_bin = shutil.which("hf") or shutil.which("huggingface-cli")
        if not hf_bin:
            return {
                "ok": False,
                "error": "hf_cli_not_found",
                "provider": "huggingface",
                "files": [str(encrypted_path), str(manifest_path)],
            }
        commands = [
            [hf_bin, "upload", repo_id, str(encrypted_path), encrypted_path.name, "--repo-type", "dataset"],
            [hf_bin, "upload", repo_id, str(manifest_path), manifest_path.name, "--repo-type", "dataset"],
        ]
        results: List[Dict[str, Any]] = []
        for command in commands:
            run = subprocess.run(command, text=True, capture_output=True, timeout=300)
            results.append(
                {
                    "command": " ".join(command[:3] + ["<file>", command[4], "--repo-type", "dataset"]),
                    "returncode": run.returncode,
                    "stdout": _redact_secret_text(run.stdout[-1000:]),
                    "stderr": _redact_secret_text(run.stderr[-1000:]),
                }
            )
            if run.returncode != 0:
                return {"ok": False, "error": "hf_upload_failed", "provider": "huggingface", "repoId": repo_id, "results": results}
        return {"ok": True, "provider": "huggingface", "repoId": repo_id, "results": results}

    def _portable_clone_hf_download(self, *, repo_id: str, filename: str) -> Dict[str, Any]:
        if not repo_id:
            return {"ok": False, "error": "repo_id_required", "provider": "huggingface"}
        if not filename:
            return {"ok": False, "error": "filename_required", "provider": "huggingface", "repoId": repo_id}
        hf_bin = shutil.which("hf") or shutil.which("huggingface-cli")
        if not hf_bin:
            return {"ok": False, "error": "hf_cli_not_found", "provider": "huggingface", "repoId": repo_id}
        out_dir = Path(tempfile.mkdtemp(prefix="total-recall-hf-download."))
        command = [hf_bin, "download", repo_id, filename, "--repo-type", "dataset", "--local-dir", str(out_dir)]
        run = subprocess.run(command, text=True, capture_output=True, timeout=300)
        path = out_dir / filename
        return {
            "ok": run.returncode == 0 and path.exists(),
            "provider": "huggingface",
            "repoId": repo_id,
            "filename": filename,
            "path": str(path),
            "returncode": run.returncode,
            "stdout": _redact_secret_text(run.stdout[-1000:]),
            "stderr": _redact_secret_text(run.stderr[-1000:]),
        }

    def _loop_index(self) -> Dict[str, Dict[str, Any]]:
        loops: Dict[str, Dict[str, Any]] = {}
        for event in self._read_events(verify_chain=True):
            metadata = event.get("metadata") or {}
            if metadata.get("schema") != LOOP_EVENT_SCHEMA_VERSION:
                continue
            loop_id = str(metadata.get("loop_id") or "")
            if not loop_id:
                continue
            event_name = str(metadata.get("loop_event") or "note")
            created = str(event.get("timestamp") or utc_now())
            current = loops.setdefault(
                loop_id,
                {
                    "loop_id": loop_id,
                    "schema": LOOP_EVENT_SCHEMA_VERSION,
                    "goal": metadata.get("goal") or "",
                    "project": metadata.get("project") or "",
                    "agent": metadata.get("agent") or "",
                    "worktree": metadata.get("worktree") or "",
                    "status": "active",
                    "phase": metadata.get("phase") or "discovery",
                    "created_at": created,
                    "updated_at": created,
                    "eventCount": 0,
                    "events": [],
                },
            )
            if event_name == "start":
                current.update(
                    {
                        "goal": metadata.get("goal") or current.get("goal") or "",
                        "project": metadata.get("project") or current.get("project") or "",
                        "agent": metadata.get("agent") or current.get("agent") or "",
                        "worktree": metadata.get("worktree") or current.get("worktree") or "",
                        "status": metadata.get("status") or "active",
                        "phase": metadata.get("phase") or current.get("phase") or "discovery",
                    }
                )
            if metadata.get("phase"):
                current["phase"] = metadata.get("phase")
            if metadata.get("status"):
                current["status"] = metadata.get("status")
            if metadata.get("verification"):
                current["lastVerification"] = metadata.get("verification")
            if metadata.get("result"):
                current["result"] = metadata.get("result")
            event_summary = {
                "event_id": event.get("event_id"),
                "ledgerRef": f"ledger:{event.get('event_id')}",
                "event": event_name,
                "timestamp": created,
                "text": event.get("text"),
                "phase": metadata.get("phase"),
                "evidence": metadata.get("evidence") or [],
            }
            current.setdefault("events", []).append(event_summary)
            current["eventCount"] = len(current.get("events") or [])
            current["lastEvent"] = event_name
            current["lastEventId"] = event.get("event_id")
            current["updated_at"] = created
        return loops

    def _write_loop_index(self) -> None:
        payload = {
            "schema": "total-recall-loop-index-v1",
            "updated_at": utc_now(),
            "loops": self._loop_index(),
        }
        self._write_json(self.home / "loops" / "index.json", payload)

    def _record_loop_event(
        self,
        loop_id: str,
        event_name: str,
        *,
        text: str,
        phase: str = "progress",
        evidence: Optional[Sequence[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        safe_loop_id = str(loop_id or "").strip()
        if not safe_loop_id:
            return {"ok": False, "error": "loop_id_required", "schema": LOOP_EVENT_SCHEMA_VERSION}
        existing = self._loop_index().get(safe_loop_id)
        if not existing:
            return {"ok": False, "error": "loop_not_found", "schema": LOOP_EVENT_SCHEMA_VERSION, "loop_id": safe_loop_id}
        body = str(text or "").strip()
        if not body:
            return {"ok": False, "error": "text_required", "schema": LOOP_EVENT_SCHEMA_VERSION, "loop_id": safe_loop_id}
        metadata = {
            "schema": LOOP_EVENT_SCHEMA_VERSION,
            "loop_id": safe_loop_id,
            "loop_event": event_name,
            "goal": existing.get("goal") or "",
            "project": existing.get("project") or "",
            "agent": existing.get("agent") or "",
            "worktree": existing.get("worktree") or "",
            "phase": phase,
            "evidence": list(evidence or []),
        }
        metadata.update(extra or {})
        result = self.ingest(
            kind="loop",
            text=body,
            session_id=f"loop:{safe_loop_id}",
            source=f"loop:{event_name}",
            metadata=metadata,
        )
        loop = self._loop_index().get(safe_loop_id, {})
        self._write_loop_index()
        return {"ok": True, "schema": LOOP_EVENT_SCHEMA_VERSION, "loop": loop, "event": result.get("event")}

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

    def _home_relative(self, path: Path) -> str:
        try:
            return path.expanduser().resolve().relative_to(self.home).as_posix()
        except Exception:
            return ""

    def _handoff_release_allowed(self, release: Dict[str, Any]) -> bool:
        if release.get("ok"):
            return True
        return release.get("error") == "remote_head_not_found"

    def _summarize_operation(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        summary = {
            "ok": bool(payload.get("ok")),
            "status": payload.get("status"),
            "error": payload.get("error"),
            "message": payload.get("message"),
        }
        if payload.get("headFile"):
            summary["headFile"] = payload.get("headFile")
        if payload.get("handoffFile"):
            summary["handoffFile"] = payload.get("handoffFile")
        if payload.get("packetFile"):
            summary["packetFile"] = payload.get("packetFile")
        return {key: value for key, value in summary.items() if value not in (None, "")}

    def _handoff_bootstrap_script(self, *, target: str, session_id: str, ttl_seconds: int) -> str:
        quoted_target = shlex.quote(target)
        quoted_session = shlex.quote(session_id)
        ttl = max(1, int(ttl_seconds or 3600))
        return "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "",
                "if ! command -v total-recall >/dev/null 2>&1; then",
                "  python3 -m pip install total-recall-core",
                "fi",
                "",
                f"total-recall backup pull --target {quoted_target}",
                "total-recall verify --receipts",
                "total-recall trust verify --format text",
                f"total-recall lease acquire --target {quoted_target} --ttl {ttl}",
                f"total-recall rehydrate --session-id {quoted_session} --mode resume --char-budget 12000",
                "",
            ]
        )

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
