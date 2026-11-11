"""Use Hermes' active secret scope, or the environment in standalone installs."""
from __future__ import annotations

import os


def get_secret(name: str) -> str:
    try:
        from agent.secret_scope import get_secret as hermes_get_secret
    except ModuleNotFoundError as exc:
        # Only an absent host permits standalone fallback. A missing/broken
        # secret_scope in Hermes must not bypass its profile isolation.
        if exc.name != "agent":
            raise
        return os.environ.get(name, "")
    # Do not catch scope errors or fall back when the active profile has no key.
    return hermes_get_secret(name) or ""
