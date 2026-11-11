"""Catalog entrypoint; the core ships in this plugin's src directory."""
from __future__ import annotations

import sys
from pathlib import Path


# Use only the source bundled alongside this entrypoint, not an ancestor's src
# tree or an environment-selected checkout. No PyPI release of the core is needed.
_source = Path(__file__).resolve().parent / "src"
if (_source / "total_recall_core").is_dir() and str(_source) not in sys.path:
    sys.path.insert(0, str(_source))


def __getattr__(name: str):
    # Pytest imports the repository root outside Hermes. Defer host-only imports
    # until Hermes actually requests the memory provider or calls register().
    if name == "TotalRecallMemoryProvider":
        from total_recall_core.hermes_provider import TotalRecallMemoryProvider

        return TotalRecallMemoryProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def register(ctx) -> None:
    from total_recall_core.hermes_provider import register as register_provider

    register_provider(ctx)
