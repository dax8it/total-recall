# Total Recall Future Adapters, Compaction, And Rehydration

Status: documentation-only note. Do not implement external semantic adapters yet.

## Current Memory Provider

Total Recall remains the active Hermes memory provider:

```bash
filippo memory status
sparky memory status
smarty memory status
```

Expected active provider:

```text
total-recall (local) <- active
```

The provider is local-first and profile-scoped. Its authoritative artifacts live
under:

```text
$HERMES_HOME/total-recall/
```

## Compaction Ownership

Hermes Agent controls context compaction thresholds. Total Recall does not
currently decide when compaction happens.

Hermes default config:

```yaml
compression:
  enabled: true
  threshold: 0.50
  target_ratio: 0.20
  protect_last_n: 20
```

Meaning:

- `threshold`: compress when prompt/context usage reaches 50 percent of the
  model context limit
- `target_ratio`: after compression, preserve a recent-tail budget equal to 20
  percent of the threshold budget
- `protect_last_n`: keep at least the last 20 messages uncompressed

Compression model routing is handled separately under:

```yaml
auxiliary:
  compression:
    provider: auto
    model: ""
```

## Total Recall During Compaction

Before Hermes compacts, it calls the active memory provider hook:

```python
on_pre_compress(messages)
```

The Total Recall provider currently:

1. ingests a `pre_compress` event into the ledger
2. builds a source-cited local context plan
3. returns that block to Hermes so the compressor preserves durable decisions,
   blockers, file paths, approvals, and next actions
4. records `session_switch` after Hermes rotates the session id because of
   compression

This is different from OpenBrain-style threshold ownership. Hermes owns the
compression trigger; Total Recall preserves and verifies continuity around it.

## Rehydration Ownership

Total Recall owns explicit and automatic rehydration.

Manual cycle:

```bash
total-recall verify --session-id main
total-recall rehydrate --session-id main --query "active continuity"
```

Hermes tool:

```text
total_recall_rehydrate
```

Rehydration is fail-closed:

- verify ledger hash chain
- reduce state deterministically
- compare state to checkpoint
- verify anchor exists
- verify anchor checkpoint hash and signature
- rebuild derived indexes from ledger
- only then return a context block

If verification fails, Total Recall refuses to rehydrate.

## Automatic Rehydration

Implemented as Total Recall provider policy. Hermes still owns compaction; Total
Recall decides when a verified recovery block should be injected.

Default config:

```yaml
memory:
  total-recall:
    auto_rehydrate:
      enabled: true
      context_threshold: 0.70
      cooldown_seconds: 180
      startup_cooldown_seconds: 900
      compression_count_threshold: 2
      stale_check_every_turns: 5
      max_chars: 5000
```

Triggers:

- provider initialize after Hermes startup or gateway restart
- `/new`
- `/resume`
- branch/session id changes
- after compaction
- after repeated compactions in one session
- context usage crosses 70 percent
- stale checkpoint detection
- low local continuity confidence in prefetch

Cooldown state:

```text
$HERMES_HOME/total-recall/state/auto_rehydrate.json
```

Hermes now passes context metrics into `MemoryProvider.on_turn_start()`:

```text
prompt_tokens
context_length
remaining_tokens
context_usage_ratio
model
platform
tool_count
```

If Total Recall detects a stale checkpoint but the ledger hash chain is still
valid, it creates a fresh `auto_rehydrate_<reason>` checkpoint before
rehydrating. If verification fails for tamper-like reasons, it injects a
FAIL_CLOSED warning instead of memory.

## Future Semantic Adapter Add-Ons

Deferred feature. Add later as optional derived-memory bridges:

- Hindsight: retain, recall, reflect, entity extraction, synthesis
- Honcho: peer/user/agent modeling, peer cards, context, conclusions
- Mem0: fact extraction, dedupe, profile recall, semantic search

Trust rule:

```text
external adapter result
-> external-memory/quarantine
-> review/promote/reject
-> promoted item becomes a ledger event
-> checkpoint/anchor verification covers promoted truth
```

Adapters must never write directly to state/current.json or checkpoints. External
systems are candidate generators only. Ledger/checkpoints/anchors remain the
source of truth.

## Suggested Later CLI

```bash
total-recall adapters list
total-recall adapters status
total-recall adapters enable hindsight
total-recall adapters sync --adapter hindsight
total-recall adapters import --adapter hindsight --query "StoryForge continuity"
total-recall external list --status quarantine
total-recall external promote <candidate-id>
total-recall external reject <candidate-id>
```
