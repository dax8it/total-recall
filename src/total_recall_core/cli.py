from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from total_recall_core.api import TotalRecallConfig, TotalRecallCore, resolve_default_home
else:
    from .api import TotalRecallConfig, TotalRecallCore, resolve_default_home


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="total-recall")
    parser.add_argument("--home", default="", help="Total Recall home. Defaults to $TOTAL_RECALL_HOME, $HERMES_HOME/total-recall, then ~/.total-recall.")
    parser.add_argument("--workspace", default="", help="Compatibility alias for older callers; only used when --home is omitted.")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("health")
    sub.add_parser("status")
    sub.add_parser("doctor")

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--kind", default="note")
    ingest.add_argument("--text", required=True)
    ingest.add_argument("--session-id", default="default")
    ingest.add_argument("--scope", default="private")
    ingest.add_argument("--source", default="manual")

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--max-results", type=int, default=12)
    search.add_argument("--session-id")

    grep = sub.add_parser("grep")
    grep.add_argument("query")
    grep.add_argument("--max-results", type=int, default=12)
    grep.add_argument("--session-id")

    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--session-id", default="default")
    checkpoint.add_argument("--label", default="")

    verify = sub.add_parser("verify")
    verify.add_argument("--session-id")
    verify.add_argument("--checkpoint-file")

    rehydrate = sub.add_parser("rehydrate")
    rehydrate.add_argument("--session-id", default="default")
    rehydrate.add_argument("--query", default="")
    rehydrate.add_argument("--max-results", type=int, default=8)

    reh_status = sub.add_parser("rehydrate-status")
    reh_status.add_argument("--session-key")
    reh_status.add_argument("--agent")

    context = sub.add_parser("context")
    context.add_argument("query")
    context.add_argument("--session-id", default="")
    context.add_argument("--max-results", type=int, default=5)

    index = sub.add_parser("index")
    index_sub = index.add_subparsers(dest="index_command")
    index_sub.add_parser("status")
    index_rebuild = index_sub.add_parser("rebuild")
    index_rebuild.add_argument(
        "--backend",
        action="append",
        choices=["sqlite-fts", "sqlite", "lancedb", "qmd"],
        help="Backend to rebuild. May be repeated. Defaults to all derived backends.",
    )
    index_search = index_sub.add_parser("search")
    index_search.add_argument("query")
    index_search.add_argument("--max-results", type=int, default=12)
    index_search.add_argument("--session-id")

    incidents = sub.add_parser("incidents")
    inc_sub = incidents.add_subparsers(dest="incident_command")
    inc_list = inc_sub.add_parser("list")
    inc_list.add_argument("--status", default="")
    inc_create = inc_sub.add_parser("create")
    inc_create.add_argument("--title", required=True)
    inc_create.add_argument("--severity", default="DEGRADED")
    inc_create.add_argument("--summary", default="")
    inc_note = inc_sub.add_parser("note")
    inc_note.add_argument("incident_id")
    inc_note.add_argument("--note", required=True)
    inc_resolve = inc_sub.add_parser("resolve")
    inc_resolve.add_argument("incident_id")
    inc_resolve.add_argument("--note", default="resolved")

    external = sub.add_parser("external")
    ext_sub = external.add_subparsers(dest="external_command")
    ext_ingest = ext_sub.add_parser("ingest")
    ext_ingest.add_argument("--text", required=True)
    ext_ingest.add_argument("--source", required=True)
    ext_ingest.add_argument("--source-kind", default="manual")
    ext_list = ext_sub.add_parser("list")
    ext_list.add_argument("--queue", default="quarantine")
    ext_promote = ext_sub.add_parser("promote")
    ext_promote.add_argument("external_id")
    ext_promote.add_argument("--session-id", default="default")
    ext_reject = ext_sub.add_parser("reject")
    ext_reject.add_argument("external_id")
    ext_reject.add_argument("--reason", default="")

    export = sub.add_parser("export")
    export.add_argument("--out", required=True)
    export.add_argument("--include-index", action="store_true")

    import_cmd = sub.add_parser("import")
    import_cmd.add_argument("bundle")
    import_cmd.add_argument("--replace", action="store_true")

    return parser


def _core(args: argparse.Namespace) -> TotalRecallCore:
    if args.home:
        home = Path(args.home)
    elif args.workspace:
        home = Path(args.workspace) / ".total-recall"
    else:
        home = resolve_default_home()
    return TotalRecallCore(TotalRecallConfig(home=home))


def _print(payload: dict) -> int:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok", True) else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "health"
    core = _core(args)

    if command == "health":
        return _print(core.health())
    if command == "status":
        return _print(core.health())
    if command == "doctor":
        return _print(core.doctor())
    if command == "ingest":
        return _print(core.ingest(kind=args.kind, text=args.text, session_id=args.session_id, scope=args.scope, source=args.source))
    if command in {"search", "grep"}:
        return _print(core.search(args.query, max_results=args.max_results, session_id=args.session_id))
    if command == "checkpoint":
        return _print(core.checkpoint(session_id=args.session_id, label=args.label))
    if command == "verify":
        return _print(core.verify(session_id=args.session_id, checkpoint_file=args.checkpoint_file))
    if command == "rehydrate":
        return _print(core.rehydrate(session_id=args.session_id, query=args.query, max_results=args.max_results))
    if command == "rehydrate-status":
        return _print(core.rehydrate_status(session_key=args.session_key, agent=args.agent))
    if command == "context":
        return _print(core.context_plan(args.query, session_id=args.session_id, max_results=args.max_results))
    if command == "index":
        sub = args.index_command or "status"
        if sub == "status":
            return _print(core.index_status())
        if sub == "rebuild":
            return _print(core.rebuild_index(backends=args.backend))
        if sub == "search":
            return _print(core.search(args.query, max_results=args.max_results, session_id=args.session_id))

    if command == "incidents":
        sub = args.incident_command or "list"
        if sub == "list":
            return _print(core.list_incidents(status=args.status))
        if sub == "create":
            return _print(core.create_incident(title=args.title, severity=args.severity, summary=args.summary))
        if sub == "note":
            return _print(core.update_incident(args.incident_id, note=args.note))
        if sub == "resolve":
            return _print(core.update_incident(args.incident_id, note=args.note, status="RESOLVED"))

    if command == "external":
        sub = args.external_command or "list"
        if sub == "ingest":
            return _print(core.external_ingest(text=args.text, source=args.source, source_kind=args.source_kind))
        if sub == "list":
            return _print(core.external_list(queue=args.queue))
        if sub == "promote":
            return _print(core.external_promote(args.external_id, session_id=args.session_id))
        if sub == "reject":
            return _print(core.external_reject(args.external_id, reason=args.reason))
    if command == "export":
        return _print(core.export_bundle(args.out, include_index=args.include_index))
    if command == "import":
        return _print(core.import_bundle(args.bundle, replace=args.replace))

    return _print({"ok": False, "error": f"command not implemented: {command}"})


if __name__ == "__main__":
    raise SystemExit(main())
