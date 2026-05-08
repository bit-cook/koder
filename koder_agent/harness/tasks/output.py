"""Output records for runtime tasks."""

from __future__ import annotations

from dataclasses import dataclass

from .models import TaskStatus


@dataclass(frozen=True)
class TaskOutputRecord:
    """Renderable output snapshot for a task."""

    task_id: str
    status: TaskStatus
    content: str
    updated_at: str
