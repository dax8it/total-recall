# Nightly Learning Review

Total Recall's nightly learning flow turns the useful part of a day into reviewable memory candidates without letting a cron job silently rewrite durable truth.

It is inspired by the gbrain pattern of keeping runtime, memory, and brain separate:

```text
runtime   Hermes / OpenClaw / another agent loop that wakes up and runs tools
memory    runtime-local boot behavior and conversation/session history
brain     Total Recall's ledger-derived current state, citations, freshness, graph, and review artifacts
```

Total Recall already has the ledger, compiled-truth projection, source ingest, freshness reports, temporal graph, Obsidian preview/promote, and explicit federation. The missing operational piece was a first-class overnight review artifact: candidate cards plus a wake-up diff.

## What `learning review` does

```bash
total-recall learning review --session-id nightly-learning --format text
```

The command reads the verified append-only ledger, classifies recent events, and produces:

1. **Candidate list** — what from the recent ledger might be worth keeping or acting on.
2. **Promotion decisions** — which layer each candidate belongs to.
3. **Wake-up diff** — the compact current-state changes a fresh agent should inspect tomorrow.

The output schema is `total-recall-learning-review-v1`.

By default the review is persisted under:

```text
reviews/learning/<review_id>.json
reviews/learning/latest.json
```

These files are review artifacts only. They do **not** mutate the ledger, state, checkpoints, anchors, or compiled truth.

Use `--no-persist` for a pure preview:

```bash
total-recall learning review --no-persist --format json
```

## Candidate card shape

Each candidate contains the fields needed for owner review:

```json
{
  "candidate_id": "learn_...",
  "source": {
    "source_ref": "ledger:evt_...",
    "event_hash": "...",
    "timestamp": "...",
    "scope": "private"
  },
  "whatChanged": "Decision: Project Orion billing replies now require owner approval...",
  "futureTaskAffected": "Billing/support replies involving Orion",
  "layer": "gbrain_page",
  "resolver": {
    "kind": "projects",
    "name": "Orion",
    "primaryHome": "projects/orion.md"
  },
  "targetPage": "projects/orion.md",
  "confidence": "high",
  "expiry": "review_on_new_evidence",
  "actionBoundary": {
    "permissions": "Can draft or retrieve context, but cannot promise a fix time or outcome without owner approval.",
    "nextTrigger": "before_billing_related_reply",
    "enforcement": "documented-boundary-only-runtime-tool-policy-enforces-actions"
  },
  "decision": {
    "layer": "gbrain_page",
    "promote": true,
    "compiledTruthAction": "rewrite_top_half",
    "timelineAction": "append_evidence"
  }
}
```

## Layer routing

The review routes candidates into five fates:

| Layer | Meaning | Promotion path |
| --- | --- | --- |
| `gbrain_page` | Long-lived current state of a person, company, project, concept, writing asset, or source | Promote with explicit ledger/source/document/vault workflows; compiled truth remains derived from ledger evidence |
| `runtime_startup_rule` | A behavior rule for one runtime's boot/user memory | Promote manually to that runtime's `MEMORY.md` / `USER.md` or equivalent |
| `open_loop` | A reminder, check, or time-sensitive follow-up | Put in scheduler/open-loop tooling, not compiled truth |
| `archive` | Historical value only, no clear future reuse | Keep in ledger/search; no promotion |
| `inbox` / needs triage | Ambiguous object ownership | Review and route before promotion |

This keeps `MEMORY.md` from turning into mush: behavior rules stay with the runtime, object state stays in Total Recall, reminders go to the scheduler, and raw history stays in the ledger.

## Action boundaries

The review tries to preserve scope, expiry, next trigger, and permissions when a memory could affect real-world action. Example:

```text
Can draft or retrieve context, but cannot promise a fix time or outcome without owner approval.
```

That boundary is written for the agent to read. It is not itself an enforcement layer. Hard blocking still belongs in runtime approval settings, tool policy, sandboxing, and scheduled-task configuration.

## Hermes tool

The Hermes plugin exposes the same flow as:

```text
total_recall_learning_review(session_id?, since?, limit?, persist?)
```

Use it from a cron job or manual session to produce the overnight candidate review. A safe cron prompt should still be self-contained because Hermes cron jobs start fresh sessions.

## Suggested nightly loop

1. Let Hermes/OpenClaw/etc. keep their native memory behavior.
2. Ingest useful working-context sources into Total Recall during the day.
3. Run:

   ```bash
   total-recall learning review --session-id nightly-learning --format text
   ```

4. Review `reviews/learning/latest.json` manually or with an owner-approved agent pass.
5. Promote only the chosen items through ledgered workflows.
6. Run `total-recall checkpoint --session-id nightly-learning` after real promotions, then `total-recall trust verify` for release-grade verification.

## Trust gate coverage

`total-recall trust verify` includes a fixture named `fixture_learning_review_candidate_cards`. It proves the learning review can produce candidate cards, promotion decisions, and a wake-up diff without mutating the ledger.
