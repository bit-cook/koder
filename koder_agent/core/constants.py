"""Shared runtime constants."""

from __future__ import annotations

import os

# Safety backstop for runaway agent loops, not a practical limit. High enough
# that long-running autonomous sessions never hit it in normal operation.
DEFAULT_MAX_TURNS = 5000

_MAX_TURNS_ENV = "KODER_MAX_TURNS"


def get_max_turns() -> int:
    """Resolve the per-run turn limit, overridable via ``KODER_MAX_TURNS``.

    Falls back to :data:`DEFAULT_MAX_TURNS` when the env var is unset, empty,
    non-numeric, or non-positive.
    """
    raw = os.environ.get(_MAX_TURNS_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_MAX_TURNS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_TURNS
    if value <= 0:
        return DEFAULT_MAX_TURNS
    return value
