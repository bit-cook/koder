"""Concise summaries for runtime-managed agents."""

from __future__ import annotations

import re

from .models import AgentRecord

MAX_SUMMARY_CHARS = 160


def _compact_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, *, limit: int = MAX_SUMMARY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def first_output_line(output_text: str | None) -> str:
    """Return the first meaningful line from agent output."""

    if not output_text:
        return ""
    for line in output_text.splitlines():
        compacted = _compact_text(line)
        if compacted:
            return compacted
    return _compact_text(output_text)


def summarize_agent_record(record: AgentRecord, *, output_text: str | None = None) -> str:
    """Build the user-facing one-line summary for a runtime agent."""

    label = _compact_text(record.description or record.prompt or record.profile)
    output_line = first_output_line(output_text)

    if record.state == "completed":
        detail = output_line or label or record.profile
        return _truncate(f"Completed: {detail}")
    if record.state == "failed":
        detail = _compact_text(record.error) or output_line or label or record.profile
        return _truncate(f"Failed: {detail}")
    if record.state == "cancelled":
        detail = label or record.profile
        return _truncate(f"Cancelled: {detail}")
    if record.state == "delayed":
        detail = label or record.profile
        return _truncate(f"Delayed: {detail}")
    if record.state == "in_progress":
        detail = label or record.profile
        return _truncate(f"Working: {detail}")
    detail = label or record.profile
    return _truncate(f"Ready: {detail}")
