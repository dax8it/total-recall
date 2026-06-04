# Obsidian Vault Export

Total Recall can export a local Obsidian-compatible vault as a derived reading
and graph layer over the authoritative ledger.

## Quick Start

Generate a vault:

```bash
total-recall vault export --out ~/TotalRecallVault
```

Open `~/TotalRecallVault` in Obsidian.

Regenerate the same vault after new memory arrives:

```bash
total-recall vault export --out ~/TotalRecallVault --force
```

The Obsidian-named alias is equivalent:

```bash
total-recall obsidian export --out ~/TotalRecallVault --force
```

Use `--format json` when an agent or script needs structured output.

## What Gets Exported

The vault is generated from verified Total Recall state and the derived
Knowledge Engine projection:

```text
Index.md
Compiled Truth.md
Graph Legend.md
README.md
.total-recall-vault.json
Sources/*.md
Entities/*.md
Documents/*.md
Decisions/*.md
Promises/*.md
Tasks/*.md
Timeline/*.md
```

`Sources/` contains one cited page per exported ledger event. `Entities/`
contains wikilinked pages for the derived knowledge graph. `Documents/` groups
document-ingest chunks. The category and timeline folders are convenience views
over the same ledger-backed source pages.

## Useful Options

```bash
total-recall vault export --out ~/TotalRecallVault \
  --scope public \
  --scope internal \
  --max-events 1000 \
  --max-entities 100 \
  --force
```

`--scope` can be repeated. Without it, the command uses the configured local
scope policy. The vault is local, so exporting private scope is allowed by
default, but be careful before syncing the folder to a shared cloud drive.

## Authority Model

The vault is disposable. Total Recall remains authoritative:

- ledger events hold the source memory
- checkpoints and anchors prove integrity
- search and Knowledge Engine queries cite ledger refs
- Obsidian pages are safe to delete and regenerate

The export refuses to write into a non-empty output folder unless you pass
`--force`.

## Edited Note Import

Obsidian edits are for reading, annotation, and exploration by default. They do
not become memory just because a markdown file changed.

When an edited note should become durable memory, use the explicit review loop:

```bash
total-recall vault import-preview \
  --vault ~/TotalRecallVault \
  --note "Edited Promise.md"

total-recall vault import-promote <preview-id>
```

Preview writes a review artifact under
`$TOTAL_RECALL_HOME/reviews/obsidian/` and does not write the ledger. Promotion
writes normal `obsidian_note_import` ledger events and records the promoted
review under `$TOTAL_RECALL_HOME/reviews/obsidian/promoted/`.

Use `--proposal-id` on `import-promote` to promote only selected proposals from
a preview:

```bash
total-recall vault import-promote <preview-id> --proposal-id <proposal-id>
```

That keeps Obsidian useful as a human workspace without making edited markdown a
silent memory authority.
