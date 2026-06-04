#!/usr/bin/env python3
"""Run a small, reproducible Total Recall benchmark/demo workload.

This is not a vendor leaderboard. It measures the flows Total Recall is built
for: ingest, checkpoint, fail-closed verification, cited recall, rehydrate,
export/import, and tamper detection on a synthetic local store.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from total_recall_core import TotalRecallConfig, TotalRecallCore, __version__  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _time_ms(fn: Callable[[], Any]) -> tuple[float, Any]:
    start = time.perf_counter()
    result = fn()
    return (time.perf_counter() - start) * 1000.0, result


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[index]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _md_cell(value: object) -> str:
    return str(value).replace("|", "\\|")


def _markdown(payload: dict[str, Any]) -> str:
    timings = payload["timings_ms"]
    query = payload["query_latency_ms"]
    rows = [
        ("Ingest", f"{timings['ingest_total']:.2f} ms", f"{payload['rates']['ingest_events_per_second']:.1f} events/sec"),
        ("Checkpoint", f"{timings['checkpoint']:.2f} ms", "signed anchor written"),
        ("Verify", f"{timings['verify']:.2f} ms", "ledger/checkpoint/anchor checked"),
        ("Search p50", f"{query['p50']:.2f} ms", f"p95 {query['p95']:.2f} ms over {query['runs']} runs"),
        ("Knowledge query", f"{timings['knowledge_query']:.2f} ms", "cited answer path"),
        ("Rehydrate", f"{timings['rehydrate']:.2f} ms", "verify-before-context path"),
        ("Export/import", f"{timings['export_import_round_trip']:.2f} ms", "portable bundle round trip"),
        ("Tamper detection", f"{timings['tamper_detection']:.2f} ms", payload["tamper_detection"]),
    ]
    if "trust_gate" in timings:
        rows.append(("Trust gate", f"{timings['trust_gate']:.2f} ms", payload.get("trust_gate", "not run")))

    lines = [
        "# Total Recall Benchmark Report",
        "",
        f"Generated: {payload['generated_at']}",
        f"Total Recall: {payload['total_recall_version']}",
        f"Python: {payload['python']}",
        f"Platform: {payload['platform']}",
        f"Synthetic events: {payload['events']}",
        "",
        "| Flow | Time | Meaning |",
        "|---|---:|---|",
    ]
    lines.extend(f"| {_md_cell(name)} | {_md_cell(value)} | {_md_cell(meaning)} |" for name, value, meaning in rows)
    lines.extend(
        [
            "",
            "## What this benchmark proves",
            "",
            "This workload is designed around continuity guarantees, not just retrieval speed.",
            "A useful Total Recall run should prove that memory can be ingested, checkpointed,",
            "verified, queried with citations, rehydrated only after verification, exported,",
            "imported, and made to fail closed when the ledger is tampered with.",
            "",
            "Use larger `--events` values to profile local disk/index behavior on your machine.",
        ]
    )
    return "\n".join(lines) + "\n"


def _synthetic_text(i: int) -> str:
    topic = ["brand promise", "support policy", "checkout trust", "launch decision", "customer renewal"][i % 5]
    return (
        f"Synthetic memory {i:05d}. Topic: {topic}. "
        f"Decision: Total Recall should cite ledger evidence before using {topic}. "
        f"Promise: If this fact is superseded, freshness should identify the newer source."
    )


def run(events: int, queries: int, out_dir: Path, keep_home: bool, run_trust_gate: bool) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="total-recall-bench-"))
    home = tmp / "store"
    core = TotalRecallCore(TotalRecallConfig(home=home, enable_lancedb=False, enable_qmd=False))

    timings: dict[str, float] = {}
    query_latencies: list[float] = []

    def ingest_all() -> None:
        for i in range(events):
            core.ingest(
                kind="benchmark_note",
                text=_synthetic_text(i),
                session_id="benchmark",
                scope="private",
                source="benchmark:synthetic",
                metadata={"synthetic_index": i},
            )

    timings["ingest_total"], _ = _time_ms(ingest_all)
    timings["checkpoint"], checkpoint = _time_ms(lambda: core.checkpoint(session_id="benchmark", label="benchmark"))
    timings["verify"], verification = _time_ms(lambda: core.verify(session_id="benchmark"))

    search_terms = ["brand promise", "support policy", "checkout trust", "launch decision", "customer renewal"]
    for i in range(queries):
        elapsed, result = _time_ms(lambda term=search_terms[i % len(search_terms)]: core.search(term, max_results=8))
        if not result.get("ok"):
            raise RuntimeError(f"search failed: {result}")
        query_latencies.append(elapsed)

    timings["knowledge_query"], knowledge = _time_ms(
        lambda: core.knowledge_query(query="What does Total Recall require before using a brand promise?", max_results=5)
    )
    timings["rehydrate"], rehydrate = _time_ms(
        lambda: core.rehydrate(session_id="benchmark", query="brand promise", max_results=5)
    )

    bundle = tmp / "benchmark-export.tar.gz"

    def export_import() -> dict[str, Any]:
        exported = core.export_bundle(str(bundle))
        import_home = tmp / "imported"
        imported = TotalRecallCore(TotalRecallConfig(home=import_home, enable_lancedb=False, enable_qmd=False))
        imported_result = imported.import_bundle(str(bundle))
        imported_verify = imported.verify(session_id="benchmark")
        return {"exported": exported, "imported": imported_result, "verify": imported_verify}

    timings["export_import_round_trip"], export_import_result = _time_ms(export_import)

    tamper_home = tmp / "tampered"
    shutil.copytree(home, tamper_home)
    ledger = tamper_home / "ledger" / "events.jsonl"
    lines = ledger.read_text(encoding="utf-8").splitlines()
    if lines:
        first = json.loads(lines[0])
        first["text"] = first["text"] + " TAMPERED AFTER CHECKPOINT."
        lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tampered = TotalRecallCore(TotalRecallConfig(home=tamper_home, enable_lancedb=False, enable_qmd=False))
    timings["tamper_detection"], tamper_verify = _time_ms(lambda: tampered.verify(session_id="benchmark"))

    trust_gate_result: dict[str, Any] | None = None
    if run_trust_gate:
        timings["trust_gate"], trust_gate_result = _time_ms(lambda: core.trust_gate_run(persist=True))

    state = core.reduce_state(write=False)
    payload: dict[str, Any] = {
        "schema": "total-recall-benchmark-v1",
        "generated_at": _now(),
        "total_recall_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "events": events,
        "queries": queries,
        "store_home": str(home) if keep_home else "temporary store removed after run",
        "timings_ms": timings,
        "rates": {
            "ingest_events_per_second": round(events / (timings["ingest_total"] / 1000.0), 2) if timings["ingest_total"] else 0,
        },
        "query_latency_ms": {
            "runs": len(query_latencies),
            "min": min(query_latencies) if query_latencies else 0,
            "p50": _percentile(query_latencies, 50),
            "p95": _percentile(query_latencies, 95),
            "max": max(query_latencies) if query_latencies else 0,
        },
        "state": {
            "event_count": state.get("event_count"),
            "state_hash": state.get("state_hash"),
            "last_event_hash": state.get("last_event_hash"),
        },
        "checkpoint_ok": bool(checkpoint.get("ok")),
        "verify_ok": bool(verification.get("ok")),
        "knowledge_query_ok": bool(knowledge.get("ok")),
        "rehydrate_ok": bool(rehydrate.get("ok")),
        "export_import_ok": bool(
            export_import_result["exported"].get("ok")
            and export_import_result["imported"].get("ok")
            and export_import_result["verify"].get("ok")
        ),
        "tamper_detection": "PASS: verify failed closed" if not tamper_verify.get("ok") else "FAIL: tamper was not rejected",
    }
    if trust_gate_result is not None:
        summary = trust_gate_result.get("summary", {}) or {}
        total_checks = summary.get("totalChecks", len(trust_gate_result.get("checks") or []))
        payload["trust_gate"] = (
            f"{trust_gate_result.get('status')} | "
            f"{summary.get('passed')}/"
            f"{total_checks} checks passed"
        )
        payload["trust_gate_ok"] = bool(trust_gate_result.get("ok"))

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"benchmark_{events}_events_{stamp}.json"
    md_path = out_dir / f"benchmark_{events}_events_{stamp}.md"
    latest_json = out_dir / "benchmark_latest.json"
    latest_md = out_dir / "benchmark_latest.md"
    _write_json(json_path, payload)
    md_text = _markdown(payload)
    md_path.write_text(md_text, encoding="utf-8")
    _write_json(latest_json, payload)
    latest_md.write_text(md_text, encoding="utf-8")
    payload["report_files"] = {"json": str(json_path), "markdown": str(md_path), "latest_json": str(latest_json), "latest_markdown": str(latest_md)}

    if not keep_home:
        shutil.rmtree(tmp, ignore_errors=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a synthetic Total Recall continuity benchmark.")
    parser.add_argument("--events", type=int, default=250, help="Synthetic events to ingest. Default: 250.")
    parser.add_argument("--queries", type=int, default=25, help="Search queries to time. Default: 25.")
    parser.add_argument("--out", default="reports/benchmarks", help="Report directory. Default: reports/benchmarks.")
    parser.add_argument("--keep-home", action="store_true", help="Keep the temporary benchmark store for inspection.")
    parser.add_argument("--skip-trust-gate", action="store_true", help="Skip the full trust gate timing.")
    args = parser.parse_args()

    if args.events <= 0:
        parser.error("--events must be positive")
    if args.queries <= 0:
        parser.error("--queries must be positive")

    payload = run(
        events=args.events,
        queries=args.queries,
        out_dir=Path(args.out),
        keep_home=args.keep_home,
        run_trust_gate=not args.skip_trust_gate,
    )
    print(
        "Total Recall benchmark complete: "
        f"events={payload['events']} "
        f"ingest={payload['rates']['ingest_events_per_second']} events/sec "
        f"verify={payload['timings_ms']['verify']:.2f}ms "
        f"search_p50={payload['query_latency_ms']['p50']:.2f}ms "
        f"tamper={payload['tamper_detection']}"
    )
    print(f"Reports: {payload['report_files']['latest_markdown']} and {payload['report_files']['latest_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
