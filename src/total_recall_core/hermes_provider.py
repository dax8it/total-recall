from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

def _candidate_core_src_paths() -> List[Path]:
    paths: List[Path] = []
    env_path = os.getenv("TOTAL_RECALL_CORE_SRC")
    if env_path:
        paths.append(Path(env_path).expanduser())
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "src"
        if (candidate / "total_recall_core").is_dir():
            paths.append(candidate)
    return paths


for CORE_SRC in _candidate_core_src_paths():
    if CORE_SRC.exists() and str(CORE_SRC) not in sys.path:
        sys.path.insert(0, str(CORE_SRC))

try:
    from total_recall_core import TotalRecallConfig, TotalRecallCore
except Exception as exc:  # pragma: no cover - surfaced by is_available/status
    TotalRecallConfig = None  # type: ignore[assignment]
    TotalRecallCore = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


SEARCH_SCHEMA = {
    "name": "total_recall_search",
    "description": "Search local Total Recall continuity memory, checkpoints, and incidents. Generated reports are audit artifacts and are excluded from retrieval.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Default 8, max 20."},
            "session_id": {"type": "string", "description": "Optional session id filter."},
        },
        "required": ["query"],
    },
}

STATUS_SCHEMA = {
    "name": "total_recall_status",
    "description": "Show Total Recall health, latest checkpoint/anchor, event count, and open incident count.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

CHECKPOINT_SCHEMA = {
    "name": "total_recall_checkpoint",
    "description": "Create a signed Total Recall checkpoint for the current or requested session.",
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session id. Defaults to current Hermes session."},
            "label": {"type": "string", "description": "Optional checkpoint label."},
        },
        "required": [],
    },
}

VERIFY_SCHEMA = {
    "name": "total_recall_verify",
    "description": "Verify Total Recall ledger, state, checkpoint, and signed anchor. Fails closed on tamper or missing artifacts.",
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Optional session id."},
        },
        "required": [],
    },
}

TRUST_VERIFY_SCHEMA = {
    "name": "total_recall_trust_verify",
    "description": "Run Total Recall's hard-coded trust gate: ledger/checkpoint/index persistence plus synthetic source, freshness, temporal graph, Obsidian import, federation, and Hermes bundle checks.",
    "parameters": {
        "type": "object",
        "properties": {
            "persist": {"type": "boolean", "description": "Write a durable trust-gate report. Defaults to true."},
        },
        "required": [],
    },
}

REHYDRATE_SCHEMA = {
    "name": "total_recall_rehydrate",
    "description": "Return a verified rehydrate context block with checkpoint and anchor citations.",
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session id. Defaults to current Hermes session."},
            "query": {"type": "string", "description": "Optional focus query."},
            "max_results": {"type": "integer", "description": "Default 8, max 20."},
        },
        "required": [],
    },
}

INCIDENTS_SCHEMA = {
    "name": "total_recall_incidents",
    "description": "List or update Total Recall continuity incidents.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "note", "resolve"], "description": "Default list."},
            "incident_id": {"type": "string", "description": "Incident id for note/resolve."},
            "note": {"type": "string", "description": "Note or resolution summary."},
            "status": {"type": "string", "description": "Optional status filter for list."},
        },
        "required": [],
    },
}

SOURCE_INGEST_SCHEMA = {
    "name": "total_recall_source_ingest",
    "description": "Ingest a working-context source such as a meeting, email, Slack thread, GitHub item, CRM note, ticket, calendar item, or agent transcript into Total Recall's verified ledger.",
    "parameters": {
        "type": "object",
        "properties": {
            "source_type": {"type": "string", "enum": ["agent_transcript", "calendar", "crm", "email", "github", "meeting", "slack", "ticket"], "description": "Working-context source type."},
            "text": {"type": "string", "description": "Source body to ingest."},
            "title": {"type": "string", "description": "Readable title."},
            "actor": {"type": "string", "description": "Author or actor when known."},
            "occurred_at": {"type": "string", "description": "ISO timestamp for when the source happened."},
            "participants": {"type": "array", "items": {"type": "string"}, "description": "Participants or related actors."},
            "session_id": {"type": "string", "description": "Defaults to the current Hermes session."},
            "scope": {"type": "string", "description": "Defaults to private."},
            "dry_run": {"type": "boolean", "description": "Preview without writing the ledger."},
        },
        "required": ["source_type", "text"],
    },
}

KNOWLEDGE_QUERY_SCHEMA = {
    "name": "total_recall_knowledge_query",
    "description": "Ask the Total Recall Knowledge Engine for a cited, confidence-gated memory/history answer using FTS, graph expansion, local rerank, recency, and temporal filtering.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Memory/history/continuity question."},
            "mode": {"type": "string", "enum": ["fast", "normal", "strict", "explore"], "description": "Default normal. Strict refuses low-evidence answers."},
            "session_id": {"type": "string", "description": "Optional session id filter. Defaults to current Hermes session."},
            "at_time": {"type": "string", "description": "Optional ISO timestamp for temporal 'what did we know then?' queries."},
            "max_results": {"type": "integer", "description": "Default 8, max 20."},
        },
        "required": ["query"],
    },
}

KNOWLEDGE_FRESHNESS_SCHEMA = {
    "name": "total_recall_knowledge_freshness",
    "description": "Report whether cited memory appears current, stale, or superseded for promises, decisions, customers, policies, project state, tasks, and other memory.",
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Optional entity/subject filter, e.g. brand promise or ACME renewal."},
            "category": {"type": "string", "description": "Optional category: promise, decision, customer, policy, project-state, task, or memory."},
            "at_time": {"type": "string", "description": "Optional ISO as-of timestamp."},
        },
        "required": [],
    },
}

KNOWLEDGE_STATUS_SCHEMA = {
    "name": "total_recall_knowledge_status",
    "description": "Show Total Recall Knowledge Engine index, graph, synthesis, and authority status.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

KNOWLEDGE_SYNTHESIS_STATUS_SCHEMA = {
    "name": "total_recall_knowledge_synthesis_status",
    "description": "Show latest derived/provisional Knowledge Engine synthesis status without promoting it to canonical memory.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

KNOWLEDGE_COMPILED_TRUTH_SCHEMA = {
    "name": "total_recall_knowledge_compiled_truth",
    "description": "Show or build Total Recall's human-readable compiled-truth projection. It is derived; the ledger remains authority.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["show", "status", "build"], "description": "Default show."},
            "format": {"type": "string", "enum": ["json", "md", "text"], "description": "Default json for show."},
        },
        "required": [],
    },
}

KNOWLEDGE_GRAPH_INSPECT_SCHEMA = {
    "name": "total_recall_knowledge_graph_inspect",
    "description": "Inspect the evidence-locked Total Recall Knowledge Engine graph by entity or source ref.",
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Optional entity/concept name to inspect."},
            "source_ref": {"type": "string", "description": "Optional source ref such as ledger:evt_..."},
            "limit": {"type": "integer", "description": "Default 20, max 100."},
        },
        "required": [],
    },
}

KNOWLEDGE_GRAPH_TIMELINE_SCHEMA = {
    "name": "total_recall_knowledge_graph_timeline",
    "description": "Show cited memory timeline for an entity, split into what was known as-of a timestamp and what changed after it.",
    "parameters": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Entity/concept name to inspect over time."},
            "at_time": {"type": "string", "description": "Optional ISO timestamp separating as-of and after-as-of evidence."},
            "limit": {"type": "integer", "description": "Default 40, max 100."},
        },
        "required": ["entity"],
    },
}

FEDERATION_QUERY_SCHEMA = {
    "name": "total_recall_federation_query",
    "description": "Query registered Total Recall agent/workspace memories with explicit authorization and workspace-separated results.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Memory/history question."},
            "targets": {"type": "array", "items": {"type": "string"}, "description": "Registered target names. Defaults to all registered targets."},
            "authorize": {"type": "boolean", "description": "Must be true to read target memories."},
            "mode": {"type": "string", "enum": ["fast", "normal", "strict", "explore"], "description": "Default normal."},
            "max_results": {"type": "integer", "description": "Default 8, max 20."},
            "at_time": {"type": "string", "description": "Optional ISO timestamp."},
        },
        "required": ["query"],
    },
}

ALL_SCHEMAS = [
    SEARCH_SCHEMA,
    STATUS_SCHEMA,
    CHECKPOINT_SCHEMA,
    VERIFY_SCHEMA,
    TRUST_VERIFY_SCHEMA,
    REHYDRATE_SCHEMA,
    INCIDENTS_SCHEMA,
    SOURCE_INGEST_SCHEMA,
    KNOWLEDGE_QUERY_SCHEMA,
    KNOWLEDGE_FRESHNESS_SCHEMA,
    KNOWLEDGE_STATUS_SCHEMA,
    KNOWLEDGE_SYNTHESIS_STATUS_SCHEMA,
    KNOWLEDGE_COMPILED_TRUTH_SCHEMA,
    KNOWLEDGE_GRAPH_INSPECT_SCHEMA,
    KNOWLEDGE_GRAPH_TIMELINE_SCHEMA,
    FEDERATION_QUERY_SCHEMA,
]


class TotalRecallMemoryProvider(MemoryProvider):
    def __init__(self) -> None:
        self._session_id = ""
        self._hermes_home = ""
        self._home = ""
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: threading.Thread | None = None
        self._pending_rehydrate: Dict[str, Any] | None = None
        self._turn_number = 0
        self._compression_count = 0
        self._last_stale_check_turn = 0

    @property
    def name(self) -> str:
        return "total-recall"

    def is_available(self) -> bool:
        return TotalRecallCore is not None and TotalRecallConfig is not None

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id or "default"
        self._hermes_home = str(kwargs.get("hermes_home") or os.getenv("HERMES_HOME") or Path.home() / ".hermes")
        self._home = self._configured_home()
        health = self._core().health()
        if int(health.get("eventCount") or 0) > 0:
            self._schedule_auto_rehydrate(
                "startup_or_gateway_restart",
                query="active continuity after Hermes startup or gateway restart",
            )

    def system_prompt_block(self) -> str:
        return (
            "# Total Recall\n"
            "Total Recall is active as the local continuity authority. Use `total_recall_knowledge_query` for cited memory/history answers, `total_recall_search` for raw durable prior context, "
            "`total_recall_knowledge_freshness` for current/stale/superseded checks, `total_recall_knowledge_compiled_truth` for the readable ledger-derived truth projection, "
            "`total_recall_knowledge_graph_inspect` and `total_recall_knowledge_graph_timeline` for evidence-locked entity context over time, "
            "`total_recall_source_ingest` for meetings/email/Slack/GitHub/CRM/tickets/calendar/agent transcripts, "
            "`total_recall_federation_query` only when explicit cross-agent/workspace authorization is provided, "
            "`total_recall_checkpoint` before risky resets or handoffs, `total_recall_trust_verify` for release-grade hard-coded gates, and `total_recall_verify`/`total_recall_rehydrate` "
            "when continuity integrity matters. Treat returned context as source-cited recall, not as a substitute for file verification.\n"
            f"Total Recall home: {self._home or self._configured_home()}"
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        auto = self._consume_auto_rehydrate(query, session_id=session_id or self._session_id)
        if auto:
            return auto
        if not query.strip():
            return self._consume_prefetch()
        cached = self._consume_prefetch()
        if cached:
            return cached
        context = self._format_context(self._core().context_plan(query, session_id=session_id or self._session_id, max_results=5))
        if not context.strip() and query.strip():
            low_confidence = self._run_auto_rehydrate(
                "low_local_continuity_confidence",
                query=query,
                session_id=session_id or self._session_id,
            )
            if low_confidence:
                return low_confidence
        return context

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not query.strip():
            return
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            return

        def _worker() -> None:
            try:
                context = self._format_context(self._core().context_plan(query, session_id=session_id or self._session_id, max_results=5))
            except Exception as exc:
                logger.debug("total-recall prefetch failed: %s", exc)
                context = ""
            with self._prefetch_lock:
                self._prefetch_result = context

        self._prefetch_thread = threading.Thread(target=_worker, daemon=True)
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if not user_content.strip() and not assistant_content.strip():
            return
        try:
            self._core().sync_turn(
                user_content,
                assistant_content,
                session_id=session_id or self._session_id,
                metadata={"provider": "hermes.total-recall"},
            )
        except Exception as exc:
            logger.warning("total-recall sync_turn failed: %s", exc)

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        text_parts = []
        for msg in messages[-24:]:
            content = msg.get("content")
            role = msg.get("role", "unknown")
            if isinstance(content, str) and content.strip():
                text_parts.append(f"{role}: {content.strip()[:1200]}")
        if not text_parts:
            return
        try:
            self._core().ingest(
                kind="session_end",
                text="\n".join(text_parts),
                session_id=self._session_id,
                source="hermes.on_session_end",
            )
            self._core().checkpoint(session_id=self._session_id, label="session_end")
        except Exception as exc:
            logger.warning("total-recall on_session_end failed: %s", exc)

    def on_session_switch(self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False, **kwargs) -> None:
        old = self._session_id
        self._session_id = new_session_id or "default"
        reason = str(kwargs.get("reason") or ("new_session" if reset else "session_switch"))
        try:
            self._core().ingest(
                kind="session_switch",
                text=f"Session switched from {old or 'unknown'} to {self._session_id}. reset={reset}. reason={reason}",
                session_id=self._session_id,
                source="hermes.on_session_switch",
                metadata={"parent_session_id": parent_session_id, "reset": reset, "reason": reason},
            )
        except Exception as exc:
            logger.debug("total-recall session switch ingest failed: %s", exc)
        if reset:
            trigger = "after_new_session"
        elif reason == "resume":
            trigger = "after_resume"
        elif reason == "compression":
            trigger = "after_compaction"
        elif reason == "branch":
            trigger = "after_branch"
        else:
            trigger = "after_session_id_change"
        self._schedule_auto_rehydrate(
            trigger,
            query=f"continuity after Hermes {reason} from {old or 'unknown'} to {self._session_id}",
            force=trigger in {"after_new_session", "after_resume", "after_compaction"},
        )

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        self._compression_count += 1
        recent = []
        for msg in messages[-16:]:
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                recent.append(content.strip()[:600])
        query = " ".join(recent)[-1600:] or "active continuity state decisions blockers next actions"
        try:
            core = self._core()
            core.ingest(
                kind="pre_compress",
                text=query,
                session_id=self._session_id,
                source="hermes.on_pre_compress",
                metadata={"message_count": len(messages)},
            )
            block = self._format_context(core.context_plan(query, session_id=self._session_id, max_results=8))
            if self._compression_count >= self._auto_rehydrate_config().get("compression_count_threshold", 2):
                self._schedule_auto_rehydrate(
                    "after_multiple_compactions",
                    query="continuity after repeated Hermes context compactions",
                )
            return block or "Total Recall is active. Preserve durable decisions, blockers, approvals, file paths, and next actions."
        except Exception as exc:
            logger.warning("total-recall on_pre_compress failed: %s", exc)
            return "Total Recall pre-compress hook failed; preserve explicit continuity details conservatively."

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        self._turn_number = int(turn_number or 0)
        cfg = self._auto_rehydrate_config()
        if not cfg.get("enabled", True):
            return

        usage_ratio = self._context_usage_ratio(kwargs)
        if usage_ratio >= float(cfg.get("context_threshold", 0.70)):
            self._schedule_auto_rehydrate(
                "context_usage_threshold",
                query=f"continuity before answering with context usage at {usage_ratio:.0%}",
            )

        check_every = max(int(cfg.get("stale_check_every_turns", 5)), 1)
        if self._turn_number - self._last_stale_check_turn >= check_every:
            self._last_stale_check_turn = self._turn_number
            try:
                verification = self._core().verify(session_id=self._session_id)
                if not verification.get("ok") and self._is_stale_checkpoint_failure(verification):
                    self._schedule_auto_rehydrate(
                        "stale_checkpoint",
                        query="continuity after Total Recall detected a stale checkpoint",
                    )
            except Exception as exc:
                logger.debug("total-recall stale checkpoint check failed: %s", exc)

    def on_memory_write(self, action: str, target: str, content: str, metadata: Dict[str, Any] | None = None) -> None:
        if action == "remove":
            return
        try:
            self._core().ingest(
                kind=f"memory_{action}",
                text=content,
                session_id=str((metadata or {}).get("session_id") or self._session_id),
                source=f"hermes.memory.{target}",
                metadata=metadata or {},
            )
        except Exception as exc:
            logger.debug("total-recall memory mirror failed: %s", exc)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return ALL_SCHEMAS

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        try:
            result = self._handle_tool(tool_name, args or {})
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return tool_error(f"Total Recall tool failed: {exc}")

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "home",
                "description": "Total Recall home directory. Defaults to $HERMES_HOME/total-recall.",
                "default": "",
            },
            {
                "key": "auto_rehydrate.enabled",
                "description": "Automatically inject verified rehydrate context after continuity-risk events.",
                "default": "true",
            },
            {
                "key": "auto_rehydrate.context_threshold",
                "description": "Context usage ratio that triggers automatic rehydrate.",
                "default": "0.70",
            }
        ]

    def _handle_tool(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        core = self._core()
        if tool_name == "total_recall_search":
            query = str(args.get("query") or "").strip()
            if not query:
                return {"ok": False, "error": "query is required"}
            return core.search(
                query,
                max_results=min(max(int(args.get("max_results") or 8), 1), 20),
                session_id=str(args.get("session_id") or "").strip() or None,
            )
        if tool_name == "total_recall_status":
            return core.health()
        if tool_name == "total_recall_checkpoint":
            return core.checkpoint(
                session_id=str(args.get("session_id") or "").strip() or self._session_id,
                label=str(args.get("label") or ""),
            )
        if tool_name == "total_recall_verify":
            return core.verify(session_id=str(args.get("session_id") or "").strip() or self._session_id)
        if tool_name == "total_recall_trust_verify":
            return core.trust_gate_run(persist=bool(args.get("persist", True)))
        if tool_name == "total_recall_rehydrate":
            return core.rehydrate(
                session_id=str(args.get("session_id") or "").strip() or self._session_id,
                query=str(args.get("query") or ""),
                max_results=min(max(int(args.get("max_results") or 8), 1), 20),
            )
        if tool_name == "total_recall_incidents":
            action = str(args.get("action") or "list")
            if action == "list":
                return core.list_incidents(status=str(args.get("status") or ""))
            incident_id = str(args.get("incident_id") or "").strip()
            if not incident_id:
                return {"ok": False, "error": "incident_id is required"}
            if action == "note":
                return core.update_incident(incident_id, note=str(args.get("note") or ""))
            if action == "resolve":
                return core.update_incident(incident_id, note=str(args.get("note") or "resolved"), status="RESOLVED")
        if tool_name == "total_recall_source_ingest":
            source_type = str(args.get("source_type") or args.get("type") or "").strip()
            text = str(args.get("text") or "").strip()
            if not source_type:
                return {"ok": False, "error": "source_type is required"}
            if not text:
                return {"ok": False, "error": "text is required"}
            participants = args.get("participants") or []
            if isinstance(participants, str):
                participants = [item.strip() for item in participants.split(",") if item.strip()]
            return core.ingest_source(
                source_type=source_type,
                text=text,
                title=str(args.get("title") or ""),
                actor=str(args.get("actor") or ""),
                occurred_at=str(args.get("occurred_at") or ""),
                participants=participants,
                session_id=str(args.get("session_id") or "").strip() or self._session_id,
                scope=str(args.get("scope") or "private"),
                dry_run=bool(args.get("dry_run")),
            )
        if tool_name == "total_recall_knowledge_query":
            query = str(args.get("query") or "").strip()
            if not query:
                return {"ok": False, "error": "query is required"}
            return core.knowledge_query(
                query,
                mode=str(args.get("mode") or "normal"),
                session_id=str(args.get("session_id") or "").strip() or self._session_id,
                at_time=str(args.get("at_time") or ""),
                max_results=min(max(int(args.get("max_results") or 8), 1), 20),
            )
        if tool_name == "total_recall_knowledge_freshness":
            return core.knowledge_freshness_report(
                entity=str(args.get("entity") or ""),
                category=str(args.get("category") or ""),
                at_time=str(args.get("at_time") or ""),
            )
        if tool_name == "total_recall_knowledge_status":
            return core.knowledge_status()
        if tool_name == "total_recall_knowledge_synthesis_status":
            return core.knowledge_synthesize_status()
        if tool_name == "total_recall_knowledge_compiled_truth":
            action = str(args.get("action") or "show")
            if action == "status":
                return core.knowledge_compiled_truth_status()
            if action == "build":
                return core.knowledge_compiled_truth_build()
            return core.knowledge_compiled_truth_show(format_=str(args.get("format") or "json"))
        if tool_name == "total_recall_knowledge_graph_inspect":
            return core.knowledge_graph_inspect(
                entity=str(args.get("entity") or ""),
                source_ref=str(args.get("source_ref") or ""),
                limit=min(max(int(args.get("limit") or 20), 1), 100),
            )
        if tool_name == "total_recall_knowledge_graph_timeline":
            entity = str(args.get("entity") or "").strip()
            if not entity:
                return {"ok": False, "error": "entity is required"}
            return core.knowledge_graph_timeline(
                entity,
                at_time=str(args.get("at_time") or ""),
                limit=min(max(int(args.get("limit") or 40), 1), 100),
            )
        if tool_name == "total_recall_federation_query":
            query = str(args.get("query") or "").strip()
            if not query:
                return {"ok": False, "error": "query is required"}
            targets = args.get("targets")
            if isinstance(targets, str):
                targets = [item.strip() for item in targets.split(",") if item.strip()]
            return core.federation_query(
                query,
                targets=targets,
                authorize=bool(args.get("authorize")),
                mode=str(args.get("mode") or "normal"),
                max_results=min(max(int(args.get("max_results") or 8), 1), 20),
                at_time=str(args.get("at_time") or ""),
            )
        return {"ok": False, "error": f"unknown Total Recall tool: {tool_name}"}

    def _core(self) -> TotalRecallCore:
        if TotalRecallCore is None or TotalRecallConfig is None:
            raise RuntimeError(f"total_recall_core import failed: {_IMPORT_ERROR}")
        return TotalRecallCore(TotalRecallConfig(home=Path(self._configured_home())))

    def _configured_home(self) -> str:
        if self._home:
            return self._home
        try:
            from hermes_cli.config import cfg_get, load_config

            cfg = load_config()
            configured = cfg_get(cfg, "memory", "total-recall", "home", default="") or ""
            if configured:
                return str(Path(str(configured)).expanduser())
        except Exception:
            pass
        if os.getenv("TOTAL_RECALL_HOME"):
            return os.environ["TOTAL_RECALL_HOME"]
        return str(Path(self._hermes_home or os.getenv("HERMES_HOME") or Path.home() / ".hermes") / "total-recall")

    def _auto_rehydrate_config(self) -> Dict[str, Any]:
        defaults = {
            "enabled": True,
            "context_threshold": 0.70,
            "cooldown_seconds": 180,
            "startup_cooldown_seconds": 900,
            "compression_count_threshold": 2,
            "stale_check_every_turns": 5,
            "max_chars": 5000,
        }
        try:
            from hermes_cli.config import cfg_get, load_config

            cfg = load_config()
            configured = cfg_get(cfg, "memory", "total-recall", "auto_rehydrate", default={}) or {}
            if isinstance(configured, dict):
                defaults.update({k: v for k, v in configured.items() if v is not None})
        except Exception:
            pass
        defaults["enabled"] = str(defaults.get("enabled", True)).lower() not in {"0", "false", "no", "off"}
        for key in ("context_threshold",):
            try:
                defaults[key] = float(defaults[key])
            except Exception:
                defaults[key] = 0.70
        for key in ("cooldown_seconds", "startup_cooldown_seconds", "compression_count_threshold", "stale_check_every_turns", "max_chars"):
            try:
                defaults[key] = int(defaults[key])
            except Exception:
                pass
        return defaults

    def _schedule_auto_rehydrate(self, reason: str, *, query: str = "", force: bool = False) -> None:
        cfg = self._auto_rehydrate_config()
        if not cfg.get("enabled", True):
            return
        session_id = self._session_id or "default"
        if not force and not self._cooldown_allows(reason, session_id, cfg):
            return
        self._pending_rehydrate = {
            "reason": reason,
            "query": query or "active continuity state decisions blockers next actions",
            "session_id": session_id,
            "scheduled_at": time.time(),
        }

    def _consume_auto_rehydrate(self, query: str, *, session_id: str) -> str:
        pending = self._pending_rehydrate
        if not pending:
            return ""
        self._pending_rehydrate = None
        return self._run_auto_rehydrate(
            str(pending.get("reason") or "scheduled"),
            query=query or str(pending.get("query") or ""),
            session_id=session_id or str(pending.get("session_id") or self._session_id or "default"),
        )

    def _run_auto_rehydrate(self, reason: str, *, query: str, session_id: str) -> str:
        cfg = self._auto_rehydrate_config()
        if not cfg.get("enabled", True) or not self._cooldown_allows(reason, session_id, cfg):
            return ""
        core = self._core()
        verification = self._ensure_verifiable_checkpoint(core, session_id=session_id, reason=reason)
        if not verification.get("ok"):
            self._record_auto_rehydrate(reason, session_id, ok=False)
            failures = ", ".join(map(str, verification.get("failures") or [])) or "verification failed"
            return (
                "[Total Recall Auto Rehydrate]\n"
                "status: FAIL_CLOSED\n"
                f"reason: {reason}\n"
                f"session_id: {session_id}\n"
                f"failures: {failures}\n"
                "Use `total_recall_verify` before trusting prior continuity."
            )
        payload = core.rehydrate(
            session_id=session_id,
            query=query or "active continuity state decisions blockers next actions",
            max_results=8,
        )
        self._record_auto_rehydrate(reason, session_id, ok=bool(payload.get("ok")))
        if not payload.get("ok"):
            return ""
        block = str(payload.get("context_block") or "")
        header = (
            "[Total Recall Auto Rehydrate]\n"
            f"reason: {reason}\n"
            f"session_id: {session_id}\n\n"
        )
        return (header + block)[: int(cfg.get("max_chars", 5000))]

    def _ensure_verifiable_checkpoint(self, core: TotalRecallCore, *, session_id: str, reason: str) -> Dict[str, Any]:
        verification = core.verify(session_id=session_id)
        if verification.get("ok"):
            return verification
        if self._is_stale_checkpoint_failure(verification):
            try:
                core.checkpoint(session_id=session_id, label=f"auto_rehydrate_{reason}")
                return core.verify(session_id=session_id)
            except Exception as exc:
                logger.debug("total-recall auto checkpoint failed: %s", exc)
        return verification

    @staticmethod
    def _is_stale_checkpoint_failure(verification: Dict[str, Any]) -> bool:
        failures = set(map(str, verification.get("failures") or []))
        stale_only = {"checkpoint_not_found", "event_count_mismatch", "last_event_hash_mismatch", "state_hash_mismatch"}
        return bool(failures) and failures.issubset(stale_only)

    def _context_usage_ratio(self, kwargs: Dict[str, Any]) -> float:
        try:
            if kwargs.get("context_usage_ratio") is not None:
                return float(kwargs.get("context_usage_ratio") or 0)
            prompt_tokens = int(kwargs.get("prompt_tokens") or 0)
            context_length = int(kwargs.get("context_length") or 0)
            if prompt_tokens > 0 and context_length > 0:
                return prompt_tokens / context_length
        except Exception:
            return 0.0
        return 0.0

    def _policy_state_file(self) -> Path:
        return Path(self._configured_home()) / "state" / "auto_rehydrate.json"

    def _load_policy_state(self) -> Dict[str, Any]:
        path = self._policy_state_file()
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"last": {}, "events": []}

    def _save_policy_state(self, state: Dict[str, Any]) -> None:
        path = self._policy_state_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        except Exception as exc:
            logger.debug("total-recall auto rehydrate state write failed: %s", exc)

    def _cooldown_allows(self, reason: str, session_id: str, cfg: Dict[str, Any]) -> bool:
        state = self._load_policy_state()
        key = f"{session_id}:{reason}"
        last = float((state.get("last") or {}).get(key) or 0)
        cooldown = int(cfg.get("startup_cooldown_seconds", 900) if reason == "startup_or_gateway_restart" else cfg.get("cooldown_seconds", 180))
        return time.time() - last >= cooldown

    def _record_auto_rehydrate(self, reason: str, session_id: str, *, ok: bool) -> None:
        state = self._load_policy_state()
        key = f"{session_id}:{reason}"
        now = time.time()
        state.setdefault("last", {})[key] = now
        events = list(state.get("events") or [])
        events.append({"ts": now, "session_id": session_id, "reason": reason, "ok": ok})
        state["events"] = events[-100:]
        self._save_policy_state(state)

    def _consume_prefetch(self) -> str:
        with self._prefetch_lock:
            value = self._prefetch_result
            self._prefetch_result = ""
        return value

    def _format_context(self, payload: Dict[str, Any]) -> str:
        if not payload.get("ok"):
            return ""
        context = str(payload.get("context") or "")
        return context[:5000]


def register(ctx) -> None:
    ctx.register_memory_provider(TotalRecallMemoryProvider())
