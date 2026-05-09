# Future Derived Memory Adapters

External semantic memory adapters are planned, but they are intentionally not
part of the authoritative continuity path yet.

## Candidate Adapters

- Hindsight: retain, recall, reflect, entity extraction, and synthesis
- Honcho: user/agent peer modeling, cards, context, and conclusions
- Mem0: fact extraction, dedupe, profile recall, and semantic search

## Trust Boundary

Adapters must be candidate generators only. They may produce semantic results,
reflections, and extracted facts, but they must not write directly to the
ledger, checkpoints, anchors, or reduced state.

```text
external adapter result
-> external-memory/quarantine
-> review/promote/reject
-> promoted item becomes a ledger event
-> checkpoint/anchor verification covers promoted truth
```

## Suggested CLI

```bash
total-recall adapters list
total-recall adapters status
total-recall adapters enable hindsight
total-recall adapters sync --adapter hindsight
total-recall adapters import --adapter hindsight --query "project continuity"
total-recall external list --status quarantine
total-recall external promote <candidate-id>
total-recall external reject <candidate-id>
```

## Validation Requirements

- adapter results are quarantined by default
- promotion creates a normal ledger event with source citation metadata
- verification rebuilds derived indexes from the ledger, never from adapter state
- adapter outages do not block core ingest, checkpoint, verify, or rehydrate
- tampered adapter receipts cannot become authoritative without promotion
