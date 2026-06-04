# Document Ingest

Total Recall can ingest plain-text documents and folders as trusted continuity
memory without requiring GBrain or another knowledge-vault layer.

## Quick Start

Preview what would be imported:

```bash
total-recall documents ingest ./docs ./handoffs/brand.md --dry-run
```

Ingest the files:

```bash
total-recall documents ingest ./docs ./handoffs/brand.md
```

Ask questions afterward:

```bash
total-recall search "brand promise"
total-recall knowledge query --query "What promise did the brand make?" --format text
```

Ingest non-file working context with the source flow:

```bash
total-recall sources ingest \
  --type meeting \
  --title "Renewal Review" \
  --occurred-at 2026-01-05T12:00:00Z \
  --participant Sales \
  --participant Success \
  --text "Decision: Renewal policy is month-to-month."
```

Project imported context into an Obsidian-compatible vault:

```bash
total-recall vault export --out ~/TotalRecallVault
```

Create a checkpoint when the imported context matters:

```bash
total-recall checkpoint --session-id documents --label document-import
```

## What Gets Imported

The command accepts files and folders. Folder scans are recursive by default.
Each supported file is read as UTF-8-ish plain text, split into chunks, and
written as normal `document` events in the append-only ledger.

Use `total-recall sources ingest` for working-context source types that are not
plain document folders: `meeting`, `email`, `slack`, `github`, `crm`, `ticket`,
`calendar`, and `agent_transcript`. These are also ledger events, but they carry
source metadata such as title, actor, participants, and `occurred_at`.

Supported extensions include:

```text
.md .markdown .txt .rst .adoc .csv .tsv .json .jsonl .yaml .yml .toml
.ini .cfg .conf .html .htm .xml
```

Default folder skips include hidden paths, `.git`, `.venv`, `node_modules`,
`dist`, `build`, `__pycache__`, `.DS_Store`, and Python bytecode.

## Useful Options

```bash
total-recall documents ingest ./docs \
  --scope internal \
  --session-id brand-docs \
  --include-extension md \
  --include-extension txt \
  --exclude "drafts/*" \
  --max-file-bytes 2000000 \
  --chunk-chars 6000
```

Use `--no-recursive` to scan only the top level of a folder.

Use `--format json` when an agent or script needs structured output.

## Trust Model

Document ingest does not create a separate brain. It uses the same Total Recall
trust spine as conversation memory:

- document chunks become ledger events
- each event has a source path and file hash metadata
- search and Knowledge Engine queries can cite the imported source
- Obsidian vault export can render imported documents as linked notes
- checkpoints and verification still decide whether memory is safe to reuse
- export/import and backup include the ingested document context

## Vault Export And GBrain

Use this built-in document flow for basic context: handoffs, strategy docs,
policies, meeting notes, markdown folders, and small local knowledge packs.
Use `sources ingest` for discrete company-context events where the event time
matters for later as-of questions.
Use `total-recall vault export` when you want Obsidian-style browsing, graph
view, timeline pages, and wikilinked source/entity/document notes.

Use a dedicated brain/vault system when you need Obsidian/Logseq/Notion-style
vault migration, file watching, rich document editing workflows, or a full
markdown knowledge graph where the vault itself is a primary workspace.
