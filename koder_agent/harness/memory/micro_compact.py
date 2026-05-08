"""Micro-compaction: truncate large tool results to save context window space."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_RESULT_CHARS = 20_000
TRUNCATION_MARKER = "\n\n[... output truncated ({original} chars → {truncated} chars). Use Read tool for full content if needed.]"


@dataclass
class MicroCompactConfig:
    """Configuration for micro-compaction."""

    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS


def micro_compact_messages(
    messages: list[dict],
    config: MicroCompactConfig | None = None,
    *,
    return_savings: bool = False,
) -> list[dict] | tuple[list[dict], int]:
    """Truncate large tool result messages to save context space.

    Only modifies messages with role='tool'. Keeps the head of the content
    and appends a truncation marker.

    Args:
        messages: Conversation messages.
        config: Optional config overriding defaults.
        return_savings: If True, return (messages, chars_saved) tuple.

    Returns:
        New list of messages with truncated tool results.
        If return_savings=True, returns (messages, total_chars_saved).
    """
    if config is None:
        config = MicroCompactConfig()

    max_chars = config.max_result_chars
    result = []
    total_saved = 0

    for msg in messages:
        if msg.get("role") != "tool":
            result.append(msg)
            continue

        content = msg.get("content", "")
        if not isinstance(content, str) or len(content) <= max_chars:
            result.append(msg)
            continue

        # Truncate: keep head
        original_len = len(content)
        truncated = content[:max_chars]
        marker = TRUNCATION_MARKER.format(
            original=original_len,
            truncated=len(truncated),
        )
        new_msg = {**msg, "content": truncated + marker}
        result.append(new_msg)
        total_saved += original_len - len(truncated + marker)

    if return_savings:
        return result, total_saved
    return result
