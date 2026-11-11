from __future__ import annotations

# Standalone installs use the core installed in Hermes' Python environment.
# Catalog installs use src/__init__.py and its bundled core instead.
from total_recall_core.hermes_provider import (  # noqa: E402,F401
    ALL_SCHEMAS,
    CHECKPOINT_SCHEMA,
    FEDERATION_QUERY_SCHEMA,
    HANDOFF_EXPORT_SCHEMA,
    INCIDENTS_SCHEMA,
    KNOWLEDGE_COMPILED_TRUTH_SCHEMA,
    KNOWLEDGE_FRESHNESS_SCHEMA,
    KNOWLEDGE_GRAPH_INSPECT_SCHEMA,
    KNOWLEDGE_GRAPH_TIMELINE_SCHEMA,
    KNOWLEDGE_QUERY_SCHEMA,
    KNOWLEDGE_STATUS_SCHEMA,
    KNOWLEDGE_SYNTHESIS_STATUS_SCHEMA,
    LEARNING_REVIEW_SCHEMA,
    LOOP_COMPLETE_SCHEMA,
    LOOP_INBOX_SCHEMA,
    LOOP_NOTE_SCHEMA,
    LOOP_START_SCHEMA,
    LOOP_VERIFY_SCHEMA,
    REHYDRATE_SCHEMA,
    SEARCH_SCHEMA,
    SOURCE_INGEST_SCHEMA,
    STATUS_SCHEMA,
    TRUST_VERIFY_SCHEMA,
    VERIFY_SCHEMA,
    TotalRecallMemoryProvider,
    register,
)
