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

    trust = sub.add_parser("trust")
    trust_sub = trust.add_subparsers(dest="trust_command")
    trust_verify = trust_sub.add_parser("verify")
    trust_verify.add_argument("--format", choices=["json", "text"], default="text")
    trust_verify.add_argument("--no-persist", action="store_true", help="Run checks without writing a trust-gate report.")
    trust_status = trust_sub.add_parser("status")
    trust_status.add_argument("--format", choices=["json", "text"], default="text")

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--kind", default="note")
    ingest.add_argument("--text", required=True)
    ingest.add_argument("--session-id", default="default")
    ingest.add_argument("--scope", default="private")
    ingest.add_argument("--source", default="manual")

    documents = sub.add_parser("documents", aliases=["docs"])
    documents_sub = documents.add_subparsers(dest="documents_command")
    documents_ingest = documents_sub.add_parser("ingest")
    documents_ingest.add_argument("paths", nargs="+", help="Files or folders to ingest as document context.")
    documents_ingest.add_argument("--session-id", default="documents", help="Session id attached to document events. Defaults to documents.")
    documents_ingest.add_argument("--scope", default="private", help="Memory scope for imported documents. Defaults to private.")
    documents_ingest.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True, help="Scan folders recursively by default.")
    documents_ingest.add_argument("--include-extension", action="append", help="File extension to include, e.g. md or .txt. May be repeated.")
    documents_ingest.add_argument("--exclude", action="append", help="Glob to skip when scanning folders. May be repeated.")
    documents_ingest.add_argument("--max-file-bytes", type=int, default=2_000_000, help="Skip files larger than this many bytes. Defaults to 2MB.")
    documents_ingest.add_argument("--chunk-chars", type=int, default=6000, help="Approximate max characters per ledger chunk. Defaults to 6000.")
    documents_ingest.add_argument("--dry-run", action="store_true", help="Preview planned/skipped files without writing ledger events.")
    documents_ingest.add_argument("--format", choices=["json", "text"], default="text", help="Human text by default; use json for scripts/agents.")

    sources = sub.add_parser("sources", aliases=["source"])
    sources_sub = sources.add_subparsers(dest="sources_command")
    sources_ingest = sources_sub.add_parser("ingest")
    sources_ingest.add_argument("--type", required=True, choices=["agent_transcript", "calendar", "crm", "email", "github", "meeting", "slack", "ticket"], help="Working-context source type.")
    sources_ingest.add_argument("--text", default="", help="Source body. Use --file for larger local source files.")
    sources_ingest.add_argument("--file", default="", help="Local text file to ingest as this source.")
    sources_ingest.add_argument("--title", default="", help="Readable title, e.g. Weekly Renewal Review.")
    sources_ingest.add_argument("--actor", default="", help="Source author/actor when known.")
    sources_ingest.add_argument("--occurred-at", default="", help="ISO timestamp for when this source happened.")
    sources_ingest.add_argument("--participant", action="append", help="Participant or related actor. May be repeated.")
    sources_ingest.add_argument("--session-id", default="working-context")
    sources_ingest.add_argument("--scope", default="private")
    sources_ingest.add_argument("--dry-run", action="store_true")
    sources_ingest.add_argument("--format", choices=["json", "text"], default="text")

    def add_vault_export_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("--out", required=True, help="Output folder for the generated Obsidian-compatible vault.")
        command.add_argument("--force", action="store_true", help="Replace an existing non-empty derived vault folder.")
        command.add_argument("--scope", action="append", help="Allowed scope to export. May be repeated. Defaults to configured scopes.")
        command.add_argument("--max-events", type=int, default=500, help="Maximum recent ledger events to project. Defaults to 500.")
        command.add_argument("--max-entities", type=int, default=100, help="Maximum graph entities to project. Defaults to 100.")
        command.add_argument("--format", choices=["json", "text"], default="text", help="Human text by default; use json for scripts/agents.")

    def add_vault_import_args(vault_subparsers: argparse._SubParsersAction) -> None:
        preview = vault_subparsers.add_parser("import-preview")
        preview.add_argument("--vault", required=True, help="Obsidian-compatible vault folder.")
        preview.add_argument("--note", action="append", help="Relative note path to preview. May be repeated. Defaults to all markdown notes.")
        preview.add_argument("--session-id", default="obsidian-import")
        preview.add_argument("--scope", default="private")
        preview.add_argument("--format", choices=["json", "text"], default="text")

        promote = vault_subparsers.add_parser("import-promote")
        promote.add_argument("preview_id")
        promote.add_argument("--proposal-id", action="append", help="Proposal id to promote. May be repeated. Defaults to all proposals in preview.")
        promote.add_argument("--session-id", default="")
        promote.add_argument("--scope", default="")
        promote.add_argument("--format", choices=["json", "text"], default="text")

    vault = sub.add_parser("vault")
    vault_sub = vault.add_subparsers(dest="vault_command")
    add_vault_export_args(vault_sub.add_parser("export"))
    add_vault_import_args(vault_sub)

    obsidian = sub.add_parser("obsidian")
    obsidian_sub = obsidian.add_subparsers(dest="obsidian_command")
    add_vault_export_args(obsidian_sub.add_parser("export"))
    add_vault_import_args(obsidian_sub)

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

    knowledge = sub.add_parser("knowledge")
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command")
    knowledge_sub.add_parser("status")

    knowledge_query = knowledge_sub.add_parser("query")
    knowledge_query.add_argument("--query", required=True)
    knowledge_query.add_argument("--mode", choices=["fast", "normal", "strict", "explore"], default="normal")
    knowledge_query.add_argument("--session-id", default="")
    knowledge_query.add_argument("--max-results", type=int, default=8)
    knowledge_query.add_argument("--at-time", default="")
    knowledge_query.add_argument("--scope", action="append", help="Allowed scope. May be repeated. Defaults to configured scopes.")
    knowledge_query.add_argument("--federate", action="append", help="Federated Total Recall home or workspace root. May be repeated; requires --authorize-federation.")
    knowledge_query.add_argument("--authorize-federation", action="store_true", help="Explicitly authorize read-only workspace-separated federation for this query.")
    knowledge_query.add_argument("--external-provider", action="append", help="Optional external rerank/semantic provider name. May be repeated; skipped unless --authorize-external-provider is set.")
    knowledge_query.add_argument("--authorize-external-provider", action="store_true", help="Explicitly authorize redacted/minimized external provider attempts for this query.")
    knowledge_query.add_argument("--format", choices=["json", "md", "text"], default="json")

    knowledge_index = knowledge_sub.add_parser("index")
    knowledge_index_sub = knowledge_index.add_subparsers(dest="knowledge_index_command")
    knowledge_index_sub.add_parser("status")
    knowledge_index_sub.add_parser("rebuild")

    knowledge_graph = knowledge_sub.add_parser("graph")
    knowledge_graph_sub = knowledge_graph.add_subparsers(dest="knowledge_graph_command")
    knowledge_graph_sub.add_parser("status")
    knowledge_graph_sub.add_parser("rebuild")
    knowledge_graph_inspect = knowledge_graph_sub.add_parser("inspect")
    knowledge_graph_inspect.add_argument("--entity", default="")
    knowledge_graph_inspect.add_argument("--source-ref", default="")
    knowledge_graph_inspect.add_argument("--limit", type=int, default=20)
    knowledge_graph_inspect.add_argument("--scope", action="append", help="Allowed scope. May be repeated. Defaults to configured scopes.")
    knowledge_graph_traverse = knowledge_graph_sub.add_parser("traverse")
    knowledge_graph_traverse.add_argument("--entity", required=True)
    knowledge_graph_traverse.add_argument("--depth", type=int, default=2)
    knowledge_graph_traverse.add_argument("--limit", type=int, default=40)
    knowledge_graph_traverse.add_argument("--scope", action="append", help="Allowed scope. May be repeated. Defaults to configured scopes.")
    knowledge_graph_timeline = knowledge_graph_sub.add_parser("timeline")
    knowledge_graph_timeline.add_argument("--entity", required=True)
    knowledge_graph_timeline.add_argument("--at-time", default="", help="Optional ISO timestamp separating as-of and after-as-of evidence.")
    knowledge_graph_timeline.add_argument("--limit", type=int, default=40)
    knowledge_graph_timeline.add_argument("--scope", action="append", help="Allowed scope. May be repeated. Defaults to configured scopes.")

    knowledge_freshness = knowledge_sub.add_parser("freshness")
    knowledge_freshness.add_argument("--entity", default="")
    knowledge_freshness.add_argument("--category", default="", help="promise, decision, customer, policy, project-state, task, or memory.")
    knowledge_freshness.add_argument("--at-time", default="")
    knowledge_freshness.add_argument("--scope", action="append", help="Allowed scope. May be repeated. Defaults to configured scopes.")
    knowledge_freshness.add_argument("--format", choices=["json", "text"], default="text")

    knowledge_truth = knowledge_sub.add_parser("truth")
    knowledge_truth_sub = knowledge_truth.add_subparsers(dest="knowledge_truth_command")
    knowledge_truth_sub.add_parser("status")
    knowledge_truth_sub.add_parser("build")
    knowledge_truth_show = knowledge_truth_sub.add_parser("show")
    knowledge_truth_show.add_argument("--format", choices=["json", "md", "text"], default="json")

    knowledge_synthesize = knowledge_sub.add_parser("synthesize")
    knowledge_synthesize_sub = knowledge_synthesize.add_subparsers(dest="knowledge_synthesize_command")
    knowledge_synthesize_sub.add_parser("status")
    knowledge_synthesize_sub.add_parser("run")
    knowledge_synthesize_promote = knowledge_synthesize_sub.add_parser("promote")
    knowledge_synthesize_promote.add_argument("proposal_id")
    knowledge_synthesize_promote.add_argument("--session-id", default="default")

    knowledge_evaluate = knowledge_sub.add_parser("evaluate")
    knowledge_evaluate_sub = knowledge_evaluate.add_subparsers(dest="knowledge_evaluate_command")
    knowledge_evaluate_sub.add_parser("run")
    knowledge_evaluate_sub.add_parser("scorecard")

    federation = sub.add_parser("federation", aliases=["agents"])
    federation_sub = federation.add_subparsers(dest="federation_command")
    federation_register = federation_sub.add_parser("register")
    federation_register.add_argument("name")
    federation_register.add_argument("path")
    federation_register.add_argument("--role", default="agent")
    federation_register.add_argument("--scope", action="append")
    federation_register.add_argument("--description", default="")
    federation_register.add_argument("--format", choices=["json", "text"], default="text")
    federation_list = federation_sub.add_parser("list")
    federation_list.add_argument("--format", choices=["json", "text"], default="text")
    federation_remove = federation_sub.add_parser("remove")
    federation_remove.add_argument("name")
    federation_remove.add_argument("--format", choices=["json", "text"], default="text")
    federation_query = federation_sub.add_parser("query")
    federation_query.add_argument("--query", required=True)
    federation_query.add_argument("--target", action="append", help="Registered target name. May be repeated. Defaults to all registered targets.")
    federation_query.add_argument("--authorize", action="store_true", help="Required to read target memories.")
    federation_query.add_argument("--mode", choices=["fast", "normal", "strict", "explore"], default="normal")
    federation_query.add_argument("--scope", action="append")
    federation_query.add_argument("--max-results", type=int, default=8)
    federation_query.add_argument("--at-time", default="")
    federation_query.add_argument("--format", choices=["json", "text"], default="text")

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

    backup = sub.add_parser("backup")
    backup_sub = backup.add_subparsers(dest="backup_command")
    backup_run = backup_sub.add_parser("run")
    backup_run.add_argument("--out-dir", default="~/total-recall-backups")
    backup_run.add_argument("--keep", type=int, default=14)
    backup_run.add_argument("--keep-days", type=int)
    backup_run.add_argument("--include-index", action="store_true")
    backup_run.add_argument("--no-checkpoint", action="store_true")
    backup_status = backup_sub.add_parser("status")
    backup_status.add_argument("--out-dir", default="~/total-recall-backups")
    backup_sync = backup_sub.add_parser("sync-status")
    backup_sync.add_argument("--out-dir", default="~/total-recall-backups")

    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8899)
    dashboard.add_argument("--backup-dir", default="~/total-recall-backups")
    dashboard.add_argument("--keep", type=int, default=14)
    dashboard.add_argument("--keep-days", type=int)

    hermes = sub.add_parser("hermes")
    hermes_sub = hermes.add_subparsers(dest="hermes_command")
    hermes_install = hermes_sub.add_parser("install")
    hermes_install.add_argument("--hermes-home", default="", help="Explicit Hermes home for nonstandard/test installs. Defaults to the Hermes global plugin root.")
    hermes_install.add_argument("--plugin-dir", default="", help="Override Hermes plugin directory. Defaults to $HERMES_PLUGIN_DIR, then ~/.hermes/plugins.")
    hermes_install.add_argument("--mode", choices=["copy", "symlink"], default="copy", help="Copy is best for end users; symlink is for repo development.")
    hermes_install.add_argument("--force", action="store_true", help="Replace an existing total-recall plugin bundle.")
    hermes_install.add_argument("--dry-run", action="store_true")
    hermes_install.add_argument("--profile", default="", help="Hermes profile to activate. If set, activation is attempted.")
    hermes_install.add_argument("--activate", action="store_true", help="Run Hermes config commands after installing the bundle.")
    hermes_install.add_argument("--hermes-bin", default="hermes", help="Hermes executable name or path.")
    hermes_install.add_argument("--hermes-python", default="", help="Override the Python executable used by Hermes. Auto-detected from the Hermes wrapper by default.")
    hermes_install.add_argument("--format", choices=["json", "text"], default="json")
    hermes_install.add_argument(
        "--core-install",
        choices=["auto", "always", "skip"],
        default="auto",
        help="Ensure total-recall-core is importable in Hermes Python. auto checks first, always reinstalls/upgrades, skip only writes the plugin bundle.",
    )
    hermes_install.add_argument("--core-source", default="", help="Path or pip spec for installing total-recall-core into Hermes Python. Defaults to this checkout when available, otherwise total-recall-core==version.")
    hermes_status = hermes_sub.add_parser("status")
    hermes_status.add_argument("--hermes-home", default="")
    hermes_status.add_argument("--plugin-dir", default="")
    hermes_status.add_argument("--hermes-bin", default="hermes")
    hermes_status.add_argument("--hermes-python", default="")
    hermes_status.add_argument("--skip-core-check", action="store_true")
    hermes_status.add_argument("--format", choices=["json", "text"], default="json")
    hermes_doctor = hermes_sub.add_parser("doctor")
    hermes_doctor.add_argument("--hermes-home", default="")
    hermes_doctor.add_argument("--plugin-dir", default="")
    hermes_doctor.add_argument("--hermes-bin", default="hermes")
    hermes_doctor.add_argument("--hermes-python", default="")
    hermes_doctor.add_argument("--format", choices=["json", "text"], default="text")
    hermes_bundle = hermes_sub.add_parser("bundle")
    hermes_bundle.add_argument("--out", required=True, help="Output .tar.gz path for a distributable Hermes plugin bundle.")
    hermes_bundle.add_argument("--force", action="store_true")

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


def _print_hermes(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    state = "ready" if payload.get("ok") else "needs attention"
    print(f"Total Recall Hermes setup: {state}")
    core = payload.get("core") or {}
    if core:
        print(f"Core runtime: {core.get('message') or core.get('status') or 'not checked'}")
        if core.get("hermesPython"):
            print(f"Hermes Python: {core['hermesPython']}")
    install = payload.get("install")
    if install:
        if install.get("ok"):
            print(f"Plugin bundle: installed at {install.get('path')}")
        else:
            print(f"Plugin bundle: {install.get('error') or 'install failed'}")
    elif payload.get("path"):
        print(f"Plugin bundle: {payload.get('path')}")
    activation = payload.get("activation")
    if activation:
        print(f"Activation: {activation.get('status')}")
    if payload.get("ready") is not None:
        print(f"Ready for Hermes: {'yes' if payload.get('ready') else 'no'}")
    next_steps = payload.get("nextSteps") or core.get("nextSteps") or []
    if next_steps:
        print("\nNext steps:")
        for step in next_steps:
            print(f"- {step}")
    return 0 if payload.get("ok", True) else 1


def _print_documents(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    state = "ready" if payload.get("ok") else "needs attention"
    print(f"Total Recall document ingest: {state}")
    print(
        f"Files ingested: {payload.get('ingestedFiles', 0)} | "
        f"Skipped: {payload.get('skippedFiles', 0)} | "
        f"Chunks: {payload.get('chunkCount', 0)}"
    )
    if payload.get("dryRun"):
        print("Dry run: no ledger events were written.")
    skipped = [item for item in payload.get("files") or [] if item.get("status") == "skipped"]
    if skipped:
        print("\nSkipped files:")
        for item in skipped[:8]:
            print(f"- {item.get('documentPath') or item.get('path')}: {item.get('reason')}")
        if len(skipped) > 8:
            print(f"- ... {len(skipped) - 8} more")
    if payload.get("events"):
        print("\nNext steps:")
        print("- total-recall search \"<topic>\"")
        print("- total-recall knowledge query --query \"<question>\" --format text")
        print("- total-recall checkpoint --session-id documents")
    return 0 if payload.get("ok", True) else 1


def _print_sources(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    if not payload.get("ok"):
        print("Total Recall source ingest: needs attention")
        print(payload.get("error") or payload.get("status") or "source ingest failed")
        if payload.get("supported"):
            print("Supported types: " + ", ".join(payload.get("supported") or []))
        return 1
    label = "planned" if payload.get("dryRun") or payload.get("status") == "DRY_RUN" else "ingested"
    print(f"Total Recall source ingest: {label}")
    print(f"Type: {payload.get('sourceType')} | Title: {payload.get('title')}")
    if payload.get("event"):
        event = payload["event"]
        print(f"Ledger event: {event.get('event_id')} | Scope: {event.get('scope')} | Source: {event.get('source')}")
    if payload.get("textPreview"):
        print("\nPreview:")
        print(payload.get("textPreview"))
    return 0


def _print_vault(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    if not payload.get("ok"):
        print("Total Recall vault export: needs attention")
        print(payload.get("error") or payload.get("status") or "export failed")
        if payload.get("path"):
            print(f"Path: {payload.get('path')}")
        for step in payload.get("nextSteps") or []:
            print(f"- {step}")
        return 1
    print("Total Recall vault export: ready")
    print(f"Vault: {payload.get('path')}")
    print(f"Manifest: {payload.get('manifest')}")
    print(
        f"Files: {payload.get('fileCount', 0)} | "
        f"Events: {payload.get('eventCount', 0)} | "
        f"Documents: {payload.get('documentCount', 0)} | "
        f"Entities: {payload.get('entityCount', 0)} | "
        f"Edges: {payload.get('edgeCount', 0)}"
    )
    print("Authority: Total Recall ledger/checkpoints/anchors. Vault notes are derived and safe to regenerate.")
    return 0


def _print_vault_import(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    if not payload.get("ok"):
        print("Total Recall vault import: needs attention")
        print(payload.get("error") or payload.get("status") or "import failed")
        return 1
    if payload.get("status") == "PREVIEW":
        print("Total Recall vault import preview: ready")
        print(f"Preview: {payload.get('preview_id')} | Proposals: {payload.get('proposalCount', 0)}")
        for proposal in (payload.get("proposals") or [])[:8]:
            print(f"- {proposal.get('proposal_id')}: {proposal.get('note')} -> {proposal.get('title')}")
        if payload.get("proposalCount", 0) > 8:
            print(f"- ... {payload.get('proposalCount', 0) - 8} more")
        print(f"Promote: {payload.get('promoteHint')}")
        return 0
    print("Total Recall vault import promote: complete")
    print(f"Preview: {payload.get('previewId')} | Ledger events: {payload.get('eventCount', 0)}")
    return 0


def _print_freshness(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    if not payload.get("ok"):
        print("Total Recall freshness: needs attention")
        print(payload.get("error") or payload.get("status") or "freshness report failed")
        return 1
    counts = payload.get("counts") or {}
    print("Total Recall freshness report")
    print(f"As of: {payload.get('asOf')}")
    print(
        "Counts: "
        + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        if counts
        else "Counts: none"
    )
    for item in (payload.get("items") or [])[:20]:
        print(
            f"- {item.get('freshness')} | {item.get('category')} | {item.get('subject')} "
            f"[{item.get('source_ref')}]"
        )
        reasons = ", ".join(item.get("reasons") or [])
        if reasons:
            print(f"  reasons: {reasons}")
    return 0


def _print_federation(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    if payload.get("targets") is not None:
        print("Total Recall federation targets")
        targets = payload.get("targets") or []
        if not targets:
            print("- No registered targets.")
        for target in targets:
            print(f"- {target.get('name')} | {target.get('role')} | {target.get('path')}")
        return 0 if payload.get("ok", True) else 1
    if payload.get("target"):
        target = payload.get("target") or {}
        print("Total Recall federation target registered")
        print(f"- {target.get('name')} | {target.get('role')} | {target.get('path')}")
        return 0
    if payload.get("removed") is not None or payload.get("status") == "MISSING":
        print(f"Total Recall federation remove: {payload.get('status')}")
        removed = payload.get("removed") or {}
        if removed:
            print(f"- {removed.get('name')} | {removed.get('path')}")
        return 0 if payload.get("ok", True) else 1
    federation = payload.get("federation") or {}
    print(payload.get("answer") or "Total Recall federation query complete.")
    print(f"Federation: {federation.get('status')} | authorized={federation.get('authorized')}")
    for workspace in federation.get("workspaces") or []:
        print(f"- {workspace.get('status')} | {workspace.get('home')} | citations={len(workspace.get('citations') or [])}")
    for warning in payload.get("warnings") or []:
        print(f"warning: {warning}")
    return 0 if payload.get("ok", True) else 1


def _print_trust_gate(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    ok = bool(payload.get("ok"))
    if payload.get("status") == "NO_TRUST_GATE":
        print("Total Recall trust gate: not run")
        print(payload.get("error") or "Run `total-recall trust verify`.")
        return 1
    print(f"Total Recall trust gate: {'pass' if ok else 'fail-closed'}")
    summary = payload.get("summary") or {}
    print(
        f"Checks: {summary.get('passed', 0)}/{summary.get('totalChecks', 0)} passed | "
        f"failed required: {summary.get('failedRequired', 0)}"
    )
    report = payload.get("report") or {}
    if report.get("json"):
        print(f"Report: {report.get('json')}")
    failed = payload.get("failedRequired") or []
    if failed:
        print("\nFailed required gates:")
        for name in failed:
            print(f"- {name}")
    return 0 if ok else 1


def _print_knowledge_query(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    if fmt == "text":
        print(payload.get("answer") or "")
        citations = payload.get("citations") or []
        if citations:
            print("\nCitations:")
            for citation in citations:
                print(f"- {citation.get('source_ref')} {citation.get('evidence_hash')}")
        return 0 if payload.get("ok", True) else 1
    print(f"# Total Recall Knowledge Query\n")
    print(payload.get("answer") or "")
    print("\n## Citations")
    for citation in payload.get("citations") or []:
        print(f"- `{citation.get('source_ref')}` evidence `{citation.get('evidence_hash')}`")
    print("\n## Confidence")
    confidence = payload.get("confidence") or {}
    print(f"- Level: `{confidence.get('level')}`")
    print(f"- Score: `{confidence.get('score')}`")
    return 0 if payload.get("ok", True) else 1


def _print_knowledge_truth(payload: dict, *, fmt: str) -> int:
    if fmt == "json":
        return _print(payload)
    print(payload.get("text") or "")
    return 0 if payload.get("ok", True) else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "health"
    if command == "hermes":
        return _handle_hermes(args)
    core = _core(args)

    if command == "health":
        return _print(core.health())
    if command == "status":
        return _print(core.health())
    if command == "doctor":
        return _print(core.doctor())
    if command == "trust":
        sub = args.trust_command or "status"
        if sub == "verify":
            return _print_trust_gate(core.trust_gate_run(persist=not args.no_persist), fmt=args.format)
        if sub == "status":
            return _print_trust_gate(core.trust_gate_status(), fmt=args.format)
    if command == "ingest":
        return _print(core.ingest(kind=args.kind, text=args.text, session_id=args.session_id, scope=args.scope, source=args.source))
    if command in {"documents", "docs"}:
        sub = args.documents_command or "ingest"
        if sub == "ingest":
            return _print_documents(
                core.ingest_documents(
                    args.paths,
                    session_id=args.session_id,
                    scope=args.scope,
                    recursive=args.recursive,
                    include_extensions=args.include_extension,
                    exclude_globs=args.exclude,
                    max_file_bytes=args.max_file_bytes,
                    chunk_chars=args.chunk_chars,
                    dry_run=args.dry_run,
                ),
                fmt=args.format,
            )
    if command in {"sources", "source"}:
        sub = args.sources_command or "ingest"
        if sub == "ingest":
            return _print_sources(
                core.ingest_source(
                    source_type=args.type,
                    text=args.text,
                    file=args.file or None,
                    title=args.title,
                    actor=args.actor,
                    occurred_at=args.occurred_at,
                    participants=args.participant,
                    session_id=args.session_id,
                    scope=args.scope,
                    dry_run=args.dry_run,
                ),
                fmt=args.format,
            )
    if command in {"vault", "obsidian"}:
        subcommand = getattr(args, f"{command}_command", "") or "export"
        if subcommand == "export":
            return _print_vault(
                core.export_obsidian_vault(
                    args.out,
                    force=args.force,
                    allowed_scopes=args.scope,
                    max_events=args.max_events,
                    max_entities=args.max_entities,
                ),
                fmt=args.format,
            )
        if subcommand == "import-preview":
            return _print_vault_import(
                core.vault_import_preview(
                    args.vault,
                    notes=args.note,
                    session_id=args.session_id,
                    scope=args.scope,
                ),
                fmt=args.format,
            )
        if subcommand == "import-promote":
            return _print_vault_import(
                core.vault_import_promote(
                    args.preview_id,
                    proposal_ids=args.proposal_id,
                    session_id=args.session_id,
                    scope=args.scope,
                ),
                fmt=args.format,
            )
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

    if command == "knowledge":
        sub = args.knowledge_command or "status"
        if sub == "status":
            return _print(core.knowledge_status())
        if sub == "query":
            return _print_knowledge_query(
                core.knowledge_query(
                    args.query,
                    mode=args.mode,
                    session_id=args.session_id,
                    max_results=args.max_results,
                    at_time=args.at_time,
                    allowed_scopes=args.scope,
                    federate=args.federate,
                    federation_authorized=args.authorize_federation,
                    external_providers=args.external_provider,
                    external_provider_authorized=args.authorize_external_provider,
                ),
                fmt=args.format,
            )
        if sub == "index":
            index_sub = args.knowledge_index_command or "status"
            if index_sub == "status":
                return _print(core.knowledge_index_status())
            if index_sub == "rebuild":
                return _print(core.knowledge_index_rebuild())
        if sub == "graph":
            graph_sub = args.knowledge_graph_command or "status"
            if graph_sub == "status":
                return _print(core.knowledge_graph_status())
            if graph_sub == "rebuild":
                return _print(core.knowledge_graph_rebuild())
            if graph_sub == "inspect":
                return _print(core.knowledge_graph_inspect(entity=args.entity, source_ref=args.source_ref, limit=args.limit, allowed_scopes=args.scope))
            if graph_sub == "traverse":
                return _print(core.knowledge_graph_traverse(args.entity, depth=args.depth, limit=args.limit, allowed_scopes=args.scope))
            if graph_sub == "timeline":
                return _print(core.knowledge_graph_timeline(args.entity, at_time=args.at_time, limit=args.limit, allowed_scopes=args.scope))
        if sub == "freshness":
            return _print_freshness(
                core.knowledge_freshness_report(
                    entity=args.entity,
                    category=args.category,
                    at_time=args.at_time,
                    allowed_scopes=args.scope,
                ),
                fmt=args.format,
            )
        if sub == "truth":
            truth_sub = args.knowledge_truth_command or "show"
            if truth_sub == "status":
                return _print(core.knowledge_compiled_truth_status())
            if truth_sub == "build":
                return _print(core.knowledge_compiled_truth_build())
            if truth_sub == "show":
                fmt = getattr(args, "format", "json")
                return _print_knowledge_truth(core.knowledge_compiled_truth_show(format_=fmt), fmt=fmt)
        if sub == "synthesize":
            synth_sub = args.knowledge_synthesize_command or "status"
            if synth_sub == "status":
                return _print(core.knowledge_synthesize_status())
            if synth_sub == "run":
                return _print(core.knowledge_synthesize_run())
            if synth_sub == "promote":
                return _print(core.knowledge_synthesize_promote(args.proposal_id, session_id=args.session_id))
        if sub == "evaluate":
            eval_sub = args.knowledge_evaluate_command or "scorecard"
            if eval_sub == "run":
                return _print(core.knowledge_evaluate_run())
            if eval_sub == "scorecard":
                return _print(core.knowledge_evaluate_scorecard())

    if command in {"federation", "agents"}:
        sub = args.federation_command or "list"
        if sub == "register":
            return _print_federation(
                core.federation_register(
                    args.name,
                    args.path,
                    role=args.role,
                    scopes=args.scope,
                    description=args.description,
                ),
                fmt=args.format,
            )
        if sub == "list":
            return _print_federation(core.federation_list(), fmt=args.format)
        if sub == "remove":
            return _print_federation(core.federation_remove(args.name), fmt=args.format)
        if sub == "query":
            return _print_federation(
                core.federation_query(
                    args.query,
                    targets=args.target,
                    authorize=args.authorize,
                    mode=args.mode,
                    allowed_scopes=args.scope,
                    max_results=args.max_results,
                    at_time=args.at_time,
                ),
                fmt=args.format,
            )

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
    if command == "backup":
        sub = args.backup_command or "status"
        if sub == "run":
            return _print(
                core.backup_run(
                    args.out_dir,
                    keep=args.keep,
                    keep_days=args.keep_days,
                    include_index=args.include_index,
                    checkpoint=not args.no_checkpoint,
                )
            )
        if sub == "status":
            return _print(core.backup_status(args.out_dir))
        if sub == "sync-status":
            return _print(core.sync_status(args.out_dir))
    if command == "dashboard":
        if __package__ in {None, ""}:
            from total_recall_core.dashboard import run_dashboard
        else:
            from .dashboard import run_dashboard

        run_dashboard(
            home=core.home,
            host=args.host,
            port=args.port,
            backup_dir=Path(args.backup_dir),
            keep=args.keep,
            keep_days=args.keep_days,
        )
        return 0

    return _print({"ok": False, "error": f"command not implemented: {command}"})


def _handle_hermes(args: argparse.Namespace) -> int:
    if __package__ in {None, ""}:
        from total_recall_core import hermes_installer
    else:
        from . import hermes_installer

    sub = args.hermes_command or "status"
    if sub == "install":
        return _print_hermes(
            hermes_installer.install_plugin(
                hermes_home=args.hermes_home,
                plugin_dir=args.plugin_dir,
                mode=args.mode,
                force=args.force,
                dry_run=args.dry_run,
                profile=args.profile,
                activate=args.activate,
                hermes_bin=args.hermes_bin,
                hermes_python=args.hermes_python,
                core_install=args.core_install,
                core_source=args.core_source,
            ),
            fmt=args.format,
        )
    if sub == "status":
        return _print_hermes(
            hermes_installer.status(
                hermes_home=args.hermes_home,
                plugin_dir=args.plugin_dir,
                hermes_bin=args.hermes_bin,
                hermes_python=args.hermes_python,
                check_core=not args.skip_core_check,
            ),
            fmt=args.format,
        )
    if sub == "doctor":
        return _print_hermes(
            hermes_installer.status(
                hermes_home=args.hermes_home,
                plugin_dir=args.plugin_dir,
                hermes_bin=args.hermes_bin,
                hermes_python=args.hermes_python,
                check_core=True,
            ),
            fmt=args.format,
        )
    if sub == "bundle":
        return _print(hermes_installer.bundle_plugin(out=args.out, force=args.force))
    return _print({"ok": False, "error": f"hermes command not implemented: {sub}"})


if __name__ == "__main__":
    raise SystemExit(main())
