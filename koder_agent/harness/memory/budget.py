"""Token budgeting helpers for runtime memory flows."""

from __future__ import annotations

import json

import tiktoken


def _encoder():
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:

        class _FallbackEncoder:
            def encode(self, text: str) -> list[int]:
                return list(text.encode("utf-8"))

        return _FallbackEncoder()


def estimate_text_tokens(text: str) -> int:
    """Estimate token count for plain text."""
    return len(_encoder().encode(text))


def estimate_message_tokens(message: dict) -> int:
    """Estimate token count for one transcript message."""
    return estimate_text_tokens(json.dumps(message, sort_keys=True, ensure_ascii=False))


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate token count for a transcript sequence."""
    return sum(estimate_message_tokens(message) for message in messages)
