# Total Recall for Hermes

This is the self-contained catalog install directory. Set the catalog entry's
`subdir` to `src`. The core ships beside this entrypoint; no checkout, ancestor
source directory, environment-selected core, or PyPI copy of the core is needed.
Hermes installs the declared `cryptography>=42,<51` dependency separately.

Requires Hermes >=0.23.1, the oldest release tested. The repository's CI pins
the real config-save and secret-scope tests to v0.23.1 at
`3b421578588243f920886e6fe79a6faa87ea026e`.

## Provider tools

`register(ctx)` registers a MemoryProvider, not direct plugin tools. Therefore
`provides_tools: []` is the truthful manifest declaration for Hermes' direct
registration comparison. The catalog entry must likewise use
`capabilities.provides_tools: []`. Selection through `memory.provider=total-recall`
activates the provider's `get_tool_schemas()` / `handle_tool_call()` interface.
It exposes these 23 tools:

- `total_recall_search`
- `total_recall_status`
- `total_recall_checkpoint`
- `total_recall_verify`
- `total_recall_trust_verify`
- `total_recall_learning_review`
- `total_recall_rehydrate`
- `total_recall_incidents`
- `total_recall_source_ingest`
- `total_recall_knowledge_query`
- `total_recall_knowledge_freshness`
- `total_recall_knowledge_status`
- `total_recall_knowledge_synthesis_status`
- `total_recall_knowledge_compiled_truth`
- `total_recall_knowledge_graph_inspect`
- `total_recall_knowledge_graph_timeline`
- `total_recall_federation_query`
- `total_recall_loop_inbox`
- `total_recall_loop_start`
- `total_recall_loop_note`
- `total_recall_loop_verify`
- `total_recall_loop_complete`
- `total_recall_handoff_export`

Portable-clone restore is not an agent tool. Federation requires explicit
authorization and keeps workspace results separate.

## Security And Side Effects

The plugin runs with Hermes' process permissions, not in a sandbox. Import and
registration do not select the memory provider or change compaction settings.
When selected, lifecycle hooks write the profile-local store at
`$HERMES_HOME/total-recall` or the configured Total Recall home. This includes
the ledger, derived indexes, signing keys, checkpoints, anchors, pending events,
rehydration state and requested reports or exports. Setup saves memory settings
through Hermes' merge-preserving config API. A daemon prefetch thread retrieves
local memory and optional indexes; it is not a service or timer.

Optional backup and portable-clone passphrases are declared in `config_schema`
with `type: secret`, `required: false` and their explicit environment names,
`TOTAL_RECALL_BACKUP_PASSPHRASE` and
`TOTAL_RECALL_PORTABLE_CLONE_PASSPHRASE`. Under Hermes, reads go through
`agent.secret_scope.get_secret`. Missing scope errors propagate; the plugin adds
no environment fallback of its own. Hermes may read the process environment in
single-profile mode, but a missing scoped value in multiplexed mode does not
fall back to another profile's environment. Only standalone use without the
Hermes `agent` package reads these variables directly.
Explicit standalone API/CLI passphrases take precedence. The dashboard uses the
same passphrase route and does not read raw HF token variables.

Subprocesses include `git` for repository metadata, optional `qmd` for derived
indexes and retrieval or explicitly enabled embeddings, and the optional `hf`
CLI. Hugging Face workflows can check CLI authentication, inspect or create
private datasets, and upload/download encrypted memory bundles with unencrypted
manifest metadata and checksums. The CLI uses the operator's existing credentials;
the plugin does not open credential files. Upload is opt-in, not a side effect of
import, enablement or ordinary turn sync. Review the target and artifacts before
using it. Downloads must pass integrity checks.

The separate standalone installer can write plugin files and, with `--activate`,
change profile settings. It may run the selected Hermes Python with
`-m pip install --upgrade <core-source>` and first `-m ensurepip --upgrade` if pip
is absent. This modifies that Python environment and pip can contact package
indexes or the source URL. It does not run on normal provider import.

Full documentation: https://github.com/dax8it/total-recall#readme
