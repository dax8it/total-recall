from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_total_recall_core() -> None:
    """Allow checkout installs while keeping the distributable plugin tiny."""
    candidates = []
    env_path = os.getenv("TOTAL_RECALL_CORE_SRC")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "src")
    for candidate in candidates:
        if (candidate / "total_recall_core").is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
            return


_bootstrap_total_recall_core()

from total_recall_core.hermes_provider import (  # noqa: E402,F401
    ALL_SCHEMAS,
    CHECKPOINT_SCHEMA,
    FEDERATION_QUERY_SCHEMA,
    INCIDENTS_SCHEMA,
    KNOWLEDGE_COMPILED_TRUTH_SCHEMA,
    KNOWLEDGE_FRESHNESS_SCHEMA,
    KNOWLEDGE_GRAPH_INSPECT_SCHEMA,
    KNOWLEDGE_GRAPH_TIMELINE_SCHEMA,
    KNOWLEDGE_QUERY_SCHEMA,
    KNOWLEDGE_STATUS_SCHEMA,
    KNOWLEDGE_SYNTHESIS_STATUS_SCHEMA,
    LEARNING_REVIEW_SCHEMA,
    REHYDRATE_SCHEMA,
    SEARCH_SCHEMA,
    SOURCE_INGEST_SCHEMA,
    STATUS_SCHEMA,
    TRUST_VERIFY_SCHEMA,
    VERIFY_SCHEMA,
    TotalRecallMemoryProvider,
    register,
)
