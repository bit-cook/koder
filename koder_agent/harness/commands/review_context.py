"""Shared local session and git context helpers for local helper commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from koder_agent.harness.commands.security_review import (
    SecurityReviewContext,
    collect_security_review_context,
)
from koder_agent.utils.client import get_model_name


@dataclass(frozen=True)
class LocalReviewContext:
    """Collected session transcript and pending git context for local helpers."""

    current_model: str
    session_transcript: str
    session_message_count: int
    git_context: SecurityReviewContext | None


def flatten_session_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            else:
                text = flatten_session_text(item)
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text.strip()
    return ""


def session_transcript_from_items(session_items: list[dict[str, Any]] | None) -> tuple[str, int]:
    if not session_items:
        return "", 0
    lines: list[str] = []
    count = 0
    for item in session_items:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue
        text = flatten_session_text(item.get("content"))
        if text:
            lines.append(f"{role}: {text}")
            count += 1
    return "\n".join(lines).strip(), count


def collect_local_review_context(
    *,
    cwd: Path | None = None,
    session_items: list[dict[str, Any]] | None = None,
) -> LocalReviewContext:
    session_transcript, session_message_count = session_transcript_from_items(session_items)
    git_context = collect_security_review_context(cwd)
    return LocalReviewContext(
        current_model=get_model_name(),
        session_transcript=session_transcript,
        session_message_count=session_message_count,
        git_context=git_context,
    )
