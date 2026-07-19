"""Shared task-delegation limit constants and strict parsing."""

from __future__ import annotations

import re

DEFAULT_TASK_DELEGATE_BATCH_SIZE = 6
DEFAULT_TASK_DELEGATE_MAX_CONCURRENCY = 4
HARD_MAX_TASK_DELEGATE_BATCH_SIZE = 32
TASK_DELEGATE_MAX_BATCH_SIZE_ENV = "KODER_TASK_DELEGATE_MAX_BATCH_SIZE"
TASK_DELEGATE_MAX_CONCURRENCY_ENV = "KODER_TASK_DELEGATE_MAX_CONCURRENCY"

_STRICT_INTEGER_TEXT = re.compile(r"[0-9]+\Z")


def parse_task_delegate_limit(value: object, *, source: str) -> int:
    """Parse one bounded task limit using the shared integer grammar.

    Configuration files may provide an integer value and environment variables
    necessarily provide text. Floats, booleans, whitespace, signs, and decimal
    spellings such as ``"2.0"`` are deliberately rejected everywhere.
    """

    if isinstance(value, bool):
        limit = None
    elif isinstance(value, int):
        limit = value
    elif isinstance(value, str) and _STRICT_INTEGER_TEXT.fullmatch(value):
        try:
            limit = int(value, 10)
        except ValueError:
            limit = None
    else:
        limit = None

    if limit is None or not 1 <= limit <= HARD_MAX_TASK_DELEGATE_BATCH_SIZE:
        raise ValueError(
            f"Invalid {source}: expected an integer between 1 and "
            f"{HARD_MAX_TASK_DELEGATE_BATCH_SIZE}, got {value!r}"
        )
    return limit
