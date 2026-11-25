# Total Recall Operational Manual

This manual is the practical guide for running Total Recall as a local-first memory continuity layer for agents and operators.

Total Recall is not just a vector database, notebook, or chat history search tool. It is a continuity authority: it records memory as append-only evidence, signs checkpoints, verifies integrity before rehydration, and exposes cited recall through CLI, Hermes tools, and the local dashboard.

## What You Can Do With It

| Job | Command or surface | What you get |
|---|---|---|
| Install or update a Hermes release | `total-recall hermes release v1.4.0 --rebuild` | Hermes PM builds from a pinned tag |
| See if memory is safe to trust | `total-recall verify` / `total-recall trust verify` | fail-closed integrity verdicts with reports |
| Rehydrate an agent after restart/compaction | `total-recall rehydrate --session-id ... --query ...` | cited context only after verification passes |
| Search local memory | `total-recall search "query"` | local cited results from ledger-derived indexes |
| Build company/project context | `total-recall documents ingest ./docs` | local files become ledger-backed memory chunks |
| Ingest working context | `total-recall sources ingest --type meeting ...` | meetings/email/Slack/GitHub/CRM/tickets/calendar evidence |
| Ask the Knowledge Engine | `total-recall knowledge query --query ...` | cited answer with freshness and graph support |
| Check stale promises/decisions | `total-recall knowledge freshness ...` | current/stale/superseded classification |
| Ask “what did we know then?” | `total-recall knowledge graph timeline --at-time ...` | as-of evidence split from later changes |
| Export a reading vault | `total-recall vault export --out ~/TotalRecallVault` | Obsidian-compatible derived notes |
| Review edited notes before memory writes | `vault import-preview` then `vault import-promote` | explicit owner-controlled promotion |
| Operate visually | `total-recall dashboard` | Trust Spine, Knowledge, Workbench, Vault, Backups |
| Back up safely | `total-recall backup run --out-dir ...` | portable archive with checkpoint/export metadata |
| Federate agents/workspaces | `federation query --authorize` | workspace-separated cited results, no silent merge |

## Mental Model

Total Recall has one authority and several projections.

Authority:

```text
ledger/events.jsonl -> state/current.json -> checkpoints/*.json -> anchors/*.json
```

Derived projections:

```text
index/                 local search caches
knowledge/             graph, compiled truth, synthesis, eval, provider reports
reports/               audit artifacts and trust gate reports
reviews/obsidian/      edited-note preview artifacts
external-memory/       quarantine/promote/reject staging
```

Rules:

1. The ledger is the source of truth.
2. Checkpoints pin reduced state, event count, and last event hash.
3. Anchors sign checkpoint hashes.
4. Verify fails closed on tamper, missing anchors, or checkpoint mismatch.
5. Rehydrate verifies before assembling context.
6. Derived indexes can be rebuilt; they are not continuity authority.
7. Generated reports are audit artifacts, not memory sources.
8. Federation and external providers require explicit authorization.

For Hermes operators, collapse compaction and rehydration into one visible **context risk zone** rule:

```text
When context enters the risk zone, save/checkpoint/verify before old chat is lost.
After compaction, reset, resume, or restart, treat only verified Total Recall as
authority. Automatic recall can cite earlier sessions from the same store. It
marks records newer than the signed checkpoint as UNVERIFIED. If verification
fails, all returned excerpts are UNVERIFIED.
Do not treat those excerpts as evidence of an approval or completed work.
```

If the Hermes profile exposes both `compression.threshold` and
`memory.total-recall.auto_rehydrate.context_threshold`, set them to the same
number for the simplest mental model. If they differ, the higher rehydrate
threshold is just an extra high-context safety net; Hermes compaction hooks still
write pre-compaction continuity.

If a reply says `Total Recall automatic recall did not finish in time`, that
turn received no Total Recall excerpts. The provider returns the notice before
Hermes' eight-second prefetch deadline; it does not wait for another store writer.
This is not a capture-gap notice. For a turn that needs verified history, use
explicit verify or rehydrate before relying on prior context.

Hermes automatic recall uses a disposable local FTS projection under
`automatic-recall/`. On the first use of a large existing store, it hash-checks
the ledger and builds this projection in the background. Until that finishes,
any excerpts from the old index and session resume packet are `UNVERIFIED`.
No migration step rewrites the ledger, a checkpoint, or an anchor. The cache
is not portable or needed in backups: a restored store can rebuild it from the
ledger. Normal capture appends update only the byte position; the projection
indexes the new records without re-reading prior ones. Explicit operations
still rebuild or verify their own state from the authority files.

Automatic `PASS` covers the signed prefix only. Newer excerpts and derived
resume-packet text are labeled `UNVERIFIED`, and a ledger edit outside the
normal append path invalidates the saved position. If the legacy index is
absent on a large store, the first request may get the same-turn notice until
the background build completes. There is no need to recreate a checkpoint to
make this projection catch up.

## First 10 Minutes

### 1. Install

```bash
pip install total-recall-core
```

From a checkout:

```bash
git clone https://github.com/dax8it/total-recall.git
cd total-recall
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[semantic]'
```

The `semantic` extra is optional. Without it, SQLite/FTS and lexical fallback still work.

### 2. Create a memory store

```bash
export TOTAL_RECALL_HOME=$(mktemp -d)
total-recall ingest --kind note --session-id demo --text "Decision: Total Recall verifies memory before rehydration."
total-recall checkpoint --session-id demo --label first-demo-checkpoint
total-recall verify --session-id demo
```

Expected shape:

```text
ok: true
status: PASS
```

### 3. Query and rehydrate

```bash
total-recall search "verifies memory"
total-recall rehydrate --session-id demo --query "verification before rehydration"
```

The explicit CLI and Hermes tool return cited rehydration only after verification
passes. The automatically injected Hermes fallback is a separate path.

### Read a captured turn

Find the event by its `ledger:` citation in `ledger/events.jsonl`. A Hermes turn
stores both full redacted text strings under `text`. If Hermes supplied tool
messages, `tool_activity` lists calls and results in order. For a packed turn,
read `tool_activity.bz2_base64`, then base64-decode, bzip2-decompress, and parse
the JSON list. Packing starts at 1,024 JSON bytes and is used only if smaller.

A result's `size_bytes` and `sha256` cover the full content Hermes handed to the
provider after redaction, not the first 96 UTF-8 bytes in `excerpt`. String
results are hashed as UTF-8. Structured results use UTF-8 JSON with sorted
keys and no extra whitespace. If Hermes already spilled or redacted the
result, the byte count and hash describe what reached the provider.

To look for the full stored message, use the original profile's `state.db` and
the event's `session_id`. Match the result's `message_uid` to
`messages.message_uid`, or use `row_id` as `messages.id`. If compaction moved
the message, look for the same UID in the session lineage. Without either ID,
match `call_id` to `messages.tool_call_id` and use `timestamp` to disambiguate.
If Hermes supplied no timestamp, `message_index` is the position in the
provider's message list, not a SQLite row ID. A deleted or never-persisted
message cannot be recovered from a pointer. Total Recall does not perform
these DB lookups during capture or recall.

SQLite search and its lexical fallback match the tool names, the first 256
UTF-8 bytes of each argument's JSON encoding, and the stored result excerpts.
Search returns the turn text and ledger citation, not the tool payload. The
knowledge graph and optional derived indexes still use turn text alone.
Session end and pre-compression events now record a marker and message count
instead of copies of the transcript. This changes new writes only.

### Capture stall watch

Run `python3 -B ~/.hermes/total-recall-releases/current/scripts/hermes_capture_watch.py`
to preview alerts without writing state or notifying anyone. A detected problem
exits with code 1. The watcher reads the default home and named profiles under
`~/.hermes`; use `--hermes-root` for a different root. It only inspects profiles
that select `memory.provider: total-recall`. It follows a static absolute
`memory.total-recall.home` or uses that profile's `total-recall` directory. It
does not load secrets or inspect Hermes process environments.

To enable it for the first time, run the preview command with `--enable-macos`
as the logged-in Mac user. For an existing checkout-based LaunchAgent, first
select the release and confirm the watcher runs in preview mode. After approval,
back up the plist and change only the script argument. Use PlistBuddy to set the
existing array entry; `plutil -replace ProgramArguments.2` can insert a second
script path instead. Run each command in order and stop on errors. Before
`bootstrap`, check that `ProgramArguments` shows exactly one watcher script
path, under `total-recall-releases/current`:

```bash
watch="$HOME/.hermes/total-recall-releases/current/scripts/hermes_capture_watch.py"
plist="$HOME/Library/LaunchAgents/io.total-recall.capture-watch.plist"
domain="gui/$(id -u)"
cp -p -n "$plist" "$plist.before-release"
launchctl bootout "$domain/io.total-recall.capture-watch"
/usr/libexec/PlistBuddy -c "Set :ProgramArguments:2 $watch" "$plist"
plutil -lint "$plist"
plutil -extract ProgramArguments json -o - "$plist"
launchctl bootstrap "$domain" "$plist"
launchctl print "$domain/io.total-recall.capture-watch"
```

If the service is not loaded, skip `bootout`. Restore the backed-up plist if
the change fails. The launcher follows `current`, so later release switches do
not require a plist edit. The watch requests one macOS Notification Center
alert per profile per stall, and rearms after capture progress. Allow
notifications for Script Editor or the sender shown by macOS and check Focus
settings. The app does not send email, Telegram, or any network notification.
If an alert says the check itself needs attention, run the preview and check
`~/Library/Logs/io.total-recall.capture-watch.log`. Notification command failures
retry on the next hourly check. Deduplication state is outside the Hermes homes
in `~/Library/Application Support/Total Recall/capture-watch.json`.

Treat this as a stall detector, not a per-turn audit or integrity check. It
detects completed replies recorded in local SQLite, not idle profiles,
transient or remote-only sessions, or intentionally unmonitored cron, subagent
and tool sessions. It looks back up to 30 days and does not try to attribute an
individual missing turn while captures are advancing. Runtime-only store paths
and shared ledgers are not supported. The ledger has only whole-second timestamps,
so a reply in the same second as the last capture is not counted as missing.
Notification delivery within a day assumes the Mac is awake, the GUI user is
logged in, and notifications are enabled.

When a stall is reported, run `hermes -p <profile> memory status`. Verify the
correct ledger before any backfill. The first post-gap reply lists an observed
date range, not a list of recovered messages. If the ledger was malformed, inspect
`state/pending_events.jsonl` as well; it has no automatic replay from the watch.

To stop it for the current login:

```bash
launchctl bootout "gui/$(id -u)/io.total-recall.capture-watch"
```

Move the matching file out of `~/Library/LaunchAgents` to keep it off after login.
Neither step touches Hermes or the ledger.

### 4. Run the trust gate

```bash
total-recall trust verify --format text
```

The trust gate checks real-store authority and isolated synthetic fixtures: source ingest, freshness, temporal timeline, Obsidian import preview/promote, federation auth, export/import, and plugin bundle surface.

### 5. Open the dashboard

```bash
total-recall dashboard --backup-dir ~/total-recall-backups
```

Open the printed URL. The default host is local-only.

## Dashboard Tour

The dashboard is the operator console.

### Trust Spine

Shows whether the store is currently safe to trust:

- Ledger hash chain
- Checkpoint freshness
- Execution trust gate
- Incident posture
- Core retrieval index
- Knowledge authority
- Backup inventory

Buttons:

- `Doctor` runs a broader health check.
- `Verify` checks ledger/checkpoint/anchor integrity.
- `Trust Gate` runs the hard-coded release/day-one execution gate.

### Knowledge Engine

Shows source/index/graph/truth/synthesis/eval health.

Buttons:

- `Rebuild Index`
- `Rebuild Graph`
- `Build Truth`
- `Run Scorecard`
- `Run Synthesis`
- `Show Truth`

### Operator Workbench

Interactive local workflows:

- Query: cited recall answer
- Graph: entity/source evidence inspection
- Freshness: current/stale/superseded checks
- Source: ingest meeting/email/Slack/GitHub/CRM/ticket/calendar text
- Truth: compiled truth projection

### Obsidian Vault Export

Exports a derived reading vault and lets you preview/promote edited notes. Promotion writes explicit ledger events; edits do not silently become memory.

### Remote Backup Providers

Shows local folder / synced folder paths that work today and planned direct provider surfaces. The local dashboard is not an OAuth-secured remote MCP server yet.

## Core Workflows

### Add a note

```bash
total-recall ingest \
  --kind note \
  --session-id project-alpha \
  --scope private \
  --text "Decision: Project Alpha will keep public docs separate from private handoffs."
```

### Ingest local documents

```bash
total-recall documents ingest ./docs ./README.md --session-id project-alpha
```

Unsupported files, binary-looking files, oversized files, and excluded paths are skipped. Text files are chunked and written as hash-chained ledger events.

### Ingest a meeting or Slack thread

```bash
total-recall sources ingest \
  --type meeting \
  --title "Project Alpha Launch Review" \
  --actor "operator" \
  --participant "design" \
  --participant "engineering" \
  --occurred-at 2026-01-05T15:00:00Z \
  --text "Decision: Launch requires a clean trust gate report."
```

### Index maintenance

Normal saves and verification refresh SQLite/FTS, not the optional LanceDB or
QMD indexes. Recall does not launch bulk embedding: it skips stale or busy
external indexes and uses SQLite/FTS or lexical fallback. This separates
interactive memory work from expensive index maintenance without removing
ledger, checkpoint, or signature checks.

Target the intended store explicitly, especially when using multiple profiles:

```bash
total-recall --home /path/to/store index status
total-recall --home /path/to/store index rebuild --backend sqlite-fts
total-recall --home /path/to/store index rebuild --backend lancedb
TOTAL_RECALL_QMD_EMBED=1 total-recall --home /path/to/store index rebuild --backend qmd
```

`--backend` may be repeated. Omitting it requests all derived backends. QMD and
LanceDB remain optional; the embedding setting is used only during an explicit
QMD rebuild. No maintenance schedule is enabled automatically.

The status field `externalRefreshPolicy` is `explicit-rebuild`. Inspect each
backend's `fresh` flag rather than treating aggregate index freshness as a
memory-integrity verdict. After a save, SQLite/FTS can be current while external
indexes are stale. That is an expected state, not proof of lost memory.

`doctor` and the trust gate require the core SQLite/FTS index to be current or
rebuildable. Their index checks retain `allBackendsFresh` and per-backend details
so optional maintenance needs stay visible. An unrebuildable core index still
fails the check. The dashboard's aggregate search-catalog indicator can remain
stale until explicit external maintenance; it does not replace signed verification.

External rebuilds serialize under a separate maintenance lock, outside the
ledger lock. They use a chain-checked snapshot; if another save advances the
ledger during the build, the completed snapshot is reported as stale. Failed
or interrupted builds remain incomplete. Timed-out QMD jobs receive bounded
cleanup of their owned process group. Do not cancel unrelated jobs or edit
index metadata to make a health check pass.

Knowledge index rebuilds stage a new database in
`knowledge/index/.knowledge.sqlite.<pid>.<nonce>.tmp`. An interrupted rebuild
can leave that file and its `-journal` behind. They are not the active
`knowledge.sqlite` and do not contain authoritative records. To reclaim space,
confirm the active database is present and no process is using the exact
staging files. Remove only those confirmed orphaned files and their matching
journals. Do not remove an in-use staging file, the active database, or any
sidecar of the active database.

Saves still perform local state reduction and SQLite/FTS work, so this is not
a constant-time or instant-save guarantee. The bounded native repair test
observed a manual save of approximately 11 seconds on its existing store;
that measurement is not a general performance benchmark or a voice-continuity
certification. The synthetic subprocess regression is in
[`tests/test_index_concurrency.py`](../tests/test_index_concurrency.py).

### Check freshness

```bash
total-recall knowledge freshness \
  --entity "launch requires a clean trust gate" \
  --category decision \
  --format text
```

### Inspect timeline

```bash
total-recall knowledge graph timeline \
  --entity "launch" \
  --at-time 2026-01-06T00:00:00Z
```

### Export and import

```bash
total-recall export --out total-recall-backup.tar.gz
total-recall import total-recall-backup.tar.gz --replace
```

Import rejects unsafe tar paths and validates manifest hashes before restoring.

### Back up on a schedule

```bash
total-recall backup run --out-dir ~/total-recall-backups --keep 14 --keep-days 90
total-recall backup status --out-dir ~/total-recall-backups
```

### Install for Hermes

```bash
total-recall hermes release v1.4.0 --rebuild
hermes -p filippo memory status
```

For the first switch from checkout or standalone installs, follow the [release
migration instructions](hermes.md#install-a-tagged-release). For development only:

```bash
./scripts/install_hermes_plugin.sh --profile filippo --activate --format text
```

### Recover missing Hermes session records

The checkout-only utility `scripts/recover_hermes_session_gap.py` appends
missing original message records from an operator-preserved JSON snapshot.
It is not an automatic session crawler, ledger-corruption repair, or a way to
invent completed turns from partial conversations.

Before using it, preserve a backup, verify the target store, and select only
records from the authorized profile. Keep snapshots and recovery output private.
The snapshot object must contain `source_database` (a stable source identifier)
and `records` (an array). Each record must include `id`, `session_id`, `role`,
`content`, and `timestamp`; preserve the original `display_kind`,
`finish_reason`, `tool_calls`, and `_compressed_summary` fields too, so filtering
does not mistake generated scaffolding for an original message.

From the repository root, preview the counts before authorizing an append:

```bash
PYTHONPATH=src python scripts/recover_hermes_session_gap.py \
  --snapshot /path/to/private/snapshot.json --home /path/to/store
```

After reviewing that plan, apply to the same intended store:

```bash
PYTHONPATH=src python scripts/recover_hermes_session_gap.py \
  --snapshot /path/to/private/snapshot.json --home /path/to/store --apply
total-recall --home /path/to/store checkpoint --label session-record-recovery
total-recall --home /path/to/store verify
```

The utility excludes generated summaries, tool-call messages, and explicitly
unfinished assistant records. It skips content already represented in captured
turns and checks recovery keys under the ledger lock to prevent replay duplicates.
Recovered records are grouped by session in private `recovered_session_records`
events with original message IDs, source timestamps, and a snapshot hash. The
ledger timestamp records recovery time; existing ledger history is not rewritten.
It does not create a checkpoint automatically. Regression coverage is in
[`tests/test_session_gap_recovery.py`](../tests/test_session_gap_recovery.py).

## What Good Looks Like

A healthy store has:

- `verify` returns `ok: true` / `PASS`.
- Latest checkpoint event count equals current ledger event count, or lag is understood and acceptable for the task.
- Anchor exists for the latest checkpoint.
- Open incident count is zero or known/triaged.
- SQLite/FTS is current or rebuildable; optional external-index staleness is
  understood and handled through explicit maintenance.
- Knowledge graph has cited entities/edges and zero uncited authority claims.
- Trust gate passes before release or handoff claims.
- Backup archive exists before machine migration or risky work.

## Fail-Closed Incidents

A fail-closed incident means Total Recall refused to pretend memory is safe.

Common causes:

- ledger event text/hash was modified
- event order changed
- checkpoint was edited
- checkpoint points at a different ledger hash
- anchor is missing or signature does not match
- trust gate required flow failed

Inspect:

```bash
total-recall incidents list
total-recall verify --format json
total-recall trust status --format json
```

Resolve only after the root cause is fixed and a fresh verify/trust gate passes.

## Safety Rules For Operators

- Do not edit `ledger/events.jsonl` by hand.
- Do not trust `index/` as source of truth.
- Do not ingest generated `reports/` back into memory.
- Do not authorize federation unless you intend to read another workspace.
- Do not send private memory to external providers without explicit authorization.
- Do not call a dashboard “remote MCP” production-ready until OAuth/scoped clients are implemented.

## Demo And Benchmark Links

- Demo guide: [demo-guide.md](demo-guide.md)
- Benchmarks: [benchmarks.md](benchmarks.md)
- Architecture: [architecture.md](architecture.md)
- Comparison: [total-recall-memory-layer-comparison-2026-06-03.md](total-recall-memory-layer-comparison-2026-06-03.md)
