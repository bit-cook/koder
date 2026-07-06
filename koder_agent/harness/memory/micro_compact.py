"""Micro-compaction: truncate large tool results to save context window space."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MAX_RESULT_CHARS = 20_000
TRUNCATION_MARKER = "\n\n[... output truncated ({original} chars → {truncated} chars). Use Read tool for full content if needed.]"

# Environment variables controlling micro-compaction.
MAX_CHARS_ENV = "KODER_MICRO_COMPACT_MAX_CHARS"
ENABLED_ENV = "KODER_MICRO_COMPACT"

_FALSEY = {"0", "false", "no", "off"}


@dataclass
class MicroCompactConfig:
    """Configuration for micro-compaction."""

    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "MicroCompactConfig":
        """Build a config from environment variables.

        - ``KODER_MICRO_COMPACT`` toggles the whole behavior. It defaults to ON;
          set it to ``0``/``false``/``no``/``off`` to disable.
        - ``KODER_MICRO_COMPACT_MAX_CHARS`` overrides the per-output character
          threshold. Invalid or non-positive values fall back to the default.
        """
        max_chars = DEFAULT_MAX_RESULT_CHARS
        raw_max = os.environ.get(MAX_CHARS_ENV)
        if raw_max is not None and raw_max.strip():
            try:
                parsed = int(raw_max.strip())
                if parsed > 0:
                    max_chars = parsed
            except ValueError:
                pass

        enabled = True
        raw_enabled = os.environ.get(ENABLED_ENV)
        if raw_enabled is not None and raw_enabled.strip().lower() in _FALSEY:
            enabled = False

        return cls(max_result_chars=max_chars, enabled=enabled)


def _truncate_text(content: str, max_chars: int) -> tuple[str, int]:
    """Truncate a string, appending the truncation marker.

    Returns the (possibly) truncated text and the number of chars saved.
    """
    original_len = len(content)
    truncated = content[:max_chars]
    marker = TRUNCATION_MARKER.format(
        original=original_len,
        truncated=len(truncated),
    )
    new_content = truncated + marker
    return new_content, original_len - len(new_content)


def micro_compact_messages(
    messages: list[dict],
    config: MicroCompactConfig | None = None,
    *,
    return_savings: bool = False,
) -> list[dict] | tuple[list[dict], int]:
    """Truncate large tool result messages to save context space.

    Handles two item shapes without ever dropping items or breaking
    tool_call/tool_result pairing:

    1. Chat-completions style: ``role == "tool"`` with a string ``content``.
    2. openai-agents / Responses style: ``type == "function_call_output"`` with
       a string ``output`` field (and a ``call_id`` that is preserved).

    Only oversized single outputs are modified; everything else (roles, ids,
    ordering, item count) is passed through untouched. Truncation keeps the head
    of the content and appends a clear marker.

    Args:
        messages: Conversation messages / session items.
        config: Optional config overriding defaults.
        return_savings: If True, return (messages, chars_saved) tuple.

    Returns:
        New list of messages with truncated tool results.
        If return_savings=True, returns (messages, total_chars_saved).
    """
    if config is None:
        config = MicroCompactConfig()

    if not config.enabled:
        result = list(messages)
        if return_savings:
            return result, 0
        return result

    max_chars = config.max_result_chars
    result: list[dict] = []
    total_saved = 0

    for msg in messages:
        if not isinstance(msg, dict):
            result.append(msg)
            continue

        # Shape 2: openai-agents function_call_output item with an 'output' field.
        if msg.get("type") == "function_call_output":
            output = msg.get("output")
            if isinstance(output, str) and len(output) > max_chars:
                new_output, saved = _truncate_text(output, max_chars)
                result.append({**msg, "output": new_output})
                total_saved += saved
            else:
                result.append(msg)
            continue

        # Shape 1: chat-completions tool role with a string 'content' field.
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > max_chars:
                new_content, saved = _truncate_text(content, max_chars)
                result.append({**msg, "content": new_content})
                total_saved += saved
            else:
                result.append(msg)
            continue

        result.append(msg)

    if return_savings:
        return result, total_saved
    return result
