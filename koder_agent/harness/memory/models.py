"""Data models for runtime transcripts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TranscriptMessage:
    """A persisted runtime transcript item."""

    id: int
    session_id: str
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


@dataclass(frozen=True)
class TranscriptSession:
    """A runtime-owned transcript session."""

    session_id: str
    name: str
    created_at: str | None = None
    updated_at: str | None = None
