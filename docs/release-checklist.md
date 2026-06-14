# Share-Readiness Checklist

Before sharing a release outside the private repo:

- confirm `LICENSE` is AGPLv3, `NOTICE` contains the original project
  attribution, and `COMMERCIAL-LICENSE.md`, `TRADEMARKS.md`, `CONTRIBUTING.md`,
  and `SECURITY.md` are present
- run `git diff --check`
- run the repository privacy scan for local absolute paths, profile-specific
  runtime directories, and sensitive configuration strings
- run `python -m pytest -q`
- verify `README.md` install steps from a fresh checkout
- verify `total-recall documents ingest <file-or-folder> --dry-run` and a real
  ingest both report ingested/skipped files clearly
- verify `total-recall sources ingest --type meeting --text <text>` writes a
  working-context source event with usable title/effective-time metadata
- verify `total-recall knowledge freshness --category promise --format text`
  reports current/stale/superseded items with citations
- verify `total-recall knowledge graph timeline --entity <name> --at-time <iso>`
  separates as-of evidence from later evidence
- verify `total-recall vault export --out <tmp>/TotalRecallVault` creates
  `Index.md`, `Compiled Truth.md`, `Graph Legend.md`, and `.total-recall-vault.json`
- verify `total-recall vault import-preview --vault <tmp>/TotalRecallVault --note <edited.md>`
  creates a review artifact without writing the ledger, then
  `total-recall vault import-promote <preview-id>` writes approved ledger events
- verify `total-recall federation register <name> <home>` plus
  `total-recall federation query --query <text> --target <name> --authorize`
  returns workspace-separated cited results
- verify `total-recall trust verify --format text` passes after creating a
  current checkpoint and writes a durable trust-gate report
- verify `total-recall hermes install --hermes-home <tmp> --force` writes a
  clean plugin bundle
- verify `total-recall hermes status` resolves the default plugin location as
  `~/.hermes/plugins/memory/total-recall` and the Hermes v0.15.x compatibility
  location as `~/.hermes/plugins/total-recall`
- verify `total-recall hermes doctor` reports the Hermes Python path and
  confirms `total-recall-core` is importable there
- verify a fresh checkout install can use
  `total-recall hermes install --core-install always --profile <profile> --activate`
  to install or refresh the core package inside Hermes' own Python environment
- verify `total-recall hermes bundle --out <tmp>/total-recall-hermes-plugin.tar.gz`
  creates a tarball containing `memory/total-recall/{__init__.py,plugin.yaml,README.md}`
  and `total-recall/{__init__.py,plugin.yaml,README.md}`
- verify the Hermes plugin loads from `hermes-plugin/total-recall` and from an
  installed copy-mode bundle
- if Hermes is available locally, verify
  `total-recall hermes install --profile <profile> --activate` followed by
  `hermes plugins list --plain --no-bundled` and
  `hermes -p <profile> memory status`
- ensure no runtime store directories are tracked
- confirm the maintainer wants this exact commit public under the
  AGPL/commercial dual-license posture described in `docs/public-launch.md`
- tag a release only after the above passes
