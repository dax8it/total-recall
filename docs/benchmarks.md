# Benchmarks And Evaluation

Total Recall should be evaluated on continuity guarantees, not only retrieval latency.

A vector database benchmark can tell you how quickly a system retrieves nearby text. It cannot tell you whether an agent should be allowed to trust that memory after a restart, compaction, import/export, profile switch, or corruption event.

Total Recall benchmarks therefore test the full continuity path:

1. ingest synthetic memory into an append-only ledger
2. reduce deterministic state
3. write signed checkpoints and anchors
4. verify ledger/checkpoint/anchor integrity
5. query/search with citations
6. rehydrate only after verification
7. export/import a portable bundle
8. prove tamper detection fails closed
9. optionally run the hard-coded trust gate

## Quick Benchmark

From the repo root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python scripts/benchmark_total_recall.py \
  --events 250 \
  --queries 25 \
  --out reports/benchmarks
```

The script writes:

```text
reports/benchmarks/benchmark_latest.json
reports/benchmarks/benchmark_latest.md
reports/benchmarks/benchmark_<events>_events_<timestamp>.json
reports/benchmarks/benchmark_<events>_events_<timestamp>.md
```

`reports/` is ignored by git. These files are local evidence artifacts, not repo source.

## Larger Runs

```bash
# Small laptop smoke
PYTHONPATH=src .venv/bin/python scripts/benchmark_total_recall.py --events 100 --queries 10

# Useful local profile
PYTHONPATH=src .venv/bin/python scripts/benchmark_total_recall.py --events 1000 --queries 50

# Stress the local store/index path
PYTHONPATH=src .venv/bin/python scripts/benchmark_total_recall.py --events 10000 --queries 100 --skip-trust-gate
```

Use `--skip-trust-gate` for large throughput runs if you only want ingest/search/verify/export timings. Use the default with trust gate when preparing release claims.

## What The Numbers Mean

| Metric | What it measures | Why it matters |
|---|---|---|
| ingest events/sec | append + state reduction + SQLite/FTS rebuild path | can the store keep up with agent/workflow events? |
| checkpoint ms | checkpoint + anchor write | can an operator create a restore point quickly? |
| verify ms | ledger/checkpoint/anchor verification | can rehydrate fail closed without blocking too long? |
| search p50/p95 | local search over derived index/fallback | can recall respond interactively? |
| knowledge query ms | cited Knowledge Engine answer path | can user-facing memory answers be grounded? |
| rehydrate ms | verify-before-context assembly | can an agent restart/compact safely? |
| export/import ms | portable bundle round trip | can memory move machines/profiles without losing authority? |
| tamper detection | intentionally modified ledger event | does the system refuse corrupted memory? |
| trust gate | 14 hard-coded release checks | can release claims be backed by executed proofs? |

## Example Output Shape

```text
Total Recall benchmark complete: events=100 ingest=100.73 events/sec verify=9.83ms search_p50=3.44ms tamper=PASS: verify failed closed
Reports: /tmp/total-recall-bench-test/benchmark_latest.md and /tmp/total-recall-bench-test/benchmark_latest.json
```

Example markdown report table:

```text
| Flow | Time | Meaning |
|---|---:|---|
| Ingest | ... ms | ... events/sec |
| Checkpoint | ... ms | signed anchor written |
| Verify | ... ms | ledger/checkpoint/anchor checked |
| Search p50 | ... ms | p95 ... ms over ... runs |
| Knowledge query | ... ms | cited answer path |
| Rehydrate | ... ms | verify-before-context path |
| Export/import | ... ms | portable bundle round trip |
| Tamper detection | ... ms | PASS: verify failed closed |
| Trust gate | ... ms | PASS | 14/14 checks passed |
```

## Benchmarking Against Other Memory Providers

Do not compare Total Recall to other memory providers with only `query latency` or `recall quality`. That misses the main product claim.

Use a continuity scorecard:

| Capability | Total Recall test | Ask other systems to prove |
|---|---|---|
| Append-only authority | ledger hash chain verify | can raw memory history be tampered with silently? |
| Signed restore points | checkpoint + Ed25519 anchor | can restore points be independently verified? |
| Fail-closed rehydrate | corrupt ledger then rehydrate/verify | will the agent refuse unsafe memory? |
| Cited recall | `knowledge query` citations | does every answer cite source evidence? |
| Freshness | superseded promise fixture | can stale commitments be flagged? |
| Temporal graph | `graph timeline --at-time` | can it separate what was known then from later changes? |
| Import/export safety | bundle round trip + unsafe tar rejection | can memory migrate without path traversal or hash drift? |
| Explicit federation | query without `--authorize` | can cross-workspace memory be read accidentally? |
| Generated-report exclusion | search should not ingest reports | does recall recursively pollute itself? |
| Operator surface | dashboard smoke | can a human see trust, incidents, backups, and query evidence? |

## Suggested Public Benchmark Video

Record a short terminal + dashboard demo, not a synthetic leaderboard.

1. Start with an empty `TOTAL_RECALL_HOME`.
2. Run `scripts/benchmark_total_recall.py --events 250 --queries 25`.
3. Open `reports/benchmarks/benchmark_latest.md`.
4. Start `total-recall dashboard --backup-dir ~/total-recall-backups`.
5. Click `Trust Gate` and show `PASS | 14/14 check(s) passed`.
6. Run a workbench query and show citations.
7. Explain tamper detection: the benchmark intentionally corrupts a copied store and verify fails closed.

Good recording tools:

```bash
# Terminal recordings
asciinema rec total-recall-benchmark.cast

# Convert terminal recording to GIF/video if desired
agg total-recall-benchmark.cast total-recall-benchmark.gif

# macOS dashboard recording
# Use QuickTime Player -> New Screen Recording
```

## Reproducibility Notes

- Reports include Python version, Total Recall version, platform, event count, timings, and hashes.
- The workload uses deterministic synthetic text; no private data is required.
- LanceDB and QMD are disabled by the benchmark script so the default run is dependency-light and deterministic.
- Benchmark report files belong under ignored `reports/`; commit only the script and docs, not local timing artifacts.
