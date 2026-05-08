"""Mailbox message models for runtime agents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AgentMessage:
    """A single message delivered to an agent mailbox."""

    agent_id: str
    content: str
    created_at: str

    @classmethod
    def create(cls, *, agent_id: str, content: str) -> "AgentMessage":
        return cls(
            agent_id=agent_id,
            content=content,
            created_at=_utc_now_iso(),
        )
