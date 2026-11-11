"""Catalog entrypoint for the bundled Total Recall core."""
from __future__ import annotations

import sys
from pathlib import Path


_source = Path(__file__).resolve().parent
if not (_source / "total_recall_core" / "__init__.py").is_file():
    raise ImportError("Total Recall catalog install is missing its bundled core")
_existing = sys.modules.get("total_recall_core")
if _existing is not None and Path(_existing.__file__ or "").resolve().parent != _source / "total_recall_core":
    raise ImportError("Total Recall core was already imported from outside this catalog install")
if not sys.path or sys.path[0] != str(_source):
    sys.path.insert(0, str(_source))


def __getattr__(name: str):
    if name == "TotalRecallMemoryProvider":
        from total_recall_core.hermes_provider import TotalRecallMemoryProvider

        return TotalRecallMemoryProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def register(ctx) -> None:
    from total_recall_core.hermes_provider import register as register_provider

    register_provider(ctx)
