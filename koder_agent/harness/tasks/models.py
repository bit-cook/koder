"""Models for runtime-managed tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

TaskStatus = Literal["pending", "in_progress", "completed"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TaskRecord:
    """A single runtime task entry."""

    id: str
    title: str
    status: TaskStatus
    created_at: str
    updated_at: str
    description: str = ""
    owner: str | None = None
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        timestamp = _utc_now_iso()
        return cls(
            id=task_id,
            title=title,
            status="pending",
            created_at=timestamp,
            updated_at=timestamp,
            description=description,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "description": self.description,
            "owner": self.owner,
            "blocks": list(self.blocks),
            "blocked_by": list(self.blocked_by),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskRecord:
        return cls(
            id=d["id"],
            title=d["title"],
            status=d.get("status", "pending"),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            description=d.get("description", ""),
            owner=d.get("owner"),
            blocks=d.get("blocks", []),
            blocked_by=d.get("blocked_by", []),
            metadata=d.get("metadata", {}),
        )
