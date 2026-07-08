"""Shared runtime constants."""

from __future__ import annotations

import os

# Safety backstop for runaway agent loops, not a practical limit. High enough
# that long-running autonomous sessions never hit it in normal operation.
DEFAULT_MAX_TURNS = 5000

_MAX_TURNS_ENV = "KODER_MAX_TURNS"

# Hard timeout (seconds) for a single Runner.run / run_streamed call.
# Prevents non-streaming or headless paths from hanging indefinitely.
DEFAULT_TURN_TIMEOUT = 600  # 10 minutes

_TURN_TIMEOUT_ENV = "KODER_TURN_TIMEOUT"


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


def get_turn_timeout() -> float:
    """Resolve per-run timeout in seconds, overridable via ``KODER_TURN_TIMEOUT``.

    Falls back to :data:`DEFAULT_TURN_TIMEOUT` when the env var is unset, empty,
    non-numeric, or non-positive. Set to ``0`` to disable (infinite wait).
    """
    raw = os.environ.get(_TURN_TIMEOUT_ENV)
    if raw is None or raw.strip() == "":
        return float(DEFAULT_TURN_TIMEOUT)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(DEFAULT_TURN_TIMEOUT)
    if value < 0:
        return float(DEFAULT_TURN_TIMEOUT)
    return value


_MAX_SESSION_COST_ENV = "KODER_MAX_SESSION_COST"


def get_max_session_cost() -> float:
    """Resolve session cost ceiling, overridable via ``KODER_MAX_SESSION_COST``.

    Returns 0.0 (disabled) when the env var is unset, empty, non-numeric, or
    non-positive. When positive, the scheduler aborts the session once
    cumulative cost exceeds this value.
    """
    raw = os.environ.get(_MAX_SESSION_COST_ENV)
    if raw is None or raw.strip() == "":
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0
