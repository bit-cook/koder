"""Denial tracking for permission decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class DenialEntry:
    tool_name: str
    reason: str
    timestamp: str


class DenialLog:
    """In-memory log of denied tool requests."""

    def __init__(self):
        self._entries: list[DenialEntry] = []

    def record(self, tool_name: str, reason: str) -> None:
        self._entries.append(
            DenialEntry(
                tool_name=tool_name,
                reason=reason,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )

    def recent(self) -> list[DenialEntry]:
        return list(self._entries)
