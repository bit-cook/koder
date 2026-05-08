"""Task lifecycle service with optional file-backed storage."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .models import TaskRecord, TaskStatus
from .output import TaskOutputRecord

if TYPE_CHECKING:
    from .storage import TaskStorage


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskService:
    """Task service supporting in-memory or file-backed storage."""

    def __init__(self, *, storage: TaskStorage | None = None):
        self._storage = storage
        self._tasks: dict[str, TaskRecord] = {}
        self._outputs: dict[str, TaskOutputRecord] = {}
        self._counter = 0

    @classmethod
    def in_memory(cls) -> TaskService:
        return cls()

    @classmethod
    def with_storage(cls, storage: TaskStorage) -> TaskService:
        return cls(storage=storage)

    def create_task(self, title: str, *, description: str = "") -> TaskRecord:
        if self._storage is not None:
            task = self._storage.create(title, description=description)
        else:
            self._counter += 1
            task_id = f"task-{uuid.uuid4().hex[:8]}"
            task = TaskRecord.create(task_id=task_id, title=title, description=description)
            self._tasks[task.id] = task

        self._outputs[task.id] = TaskOutputRecord(
            task_id=task.id,
            status=task.status,
            content=f"Task created: {task.title}",
            updated_at=task.updated_at,
        )
        return task

    def list_tasks(self) -> list[TaskRecord]:
        if self._storage is not None:
            return self._storage.list_all()
        return sorted(self._tasks.values(), key=lambda task: task.created_at)

    def get_task(self, task_id: str) -> TaskRecord:
        if self._storage is not None:
            task = self._storage.get(task_id)
            if task is None:
                raise KeyError(task_id)
            return task
        return self._tasks[task_id]

    def update_status(self, task_id: str, status: TaskStatus) -> TaskRecord:
        if self._storage is not None:
            updated = self._storage.update(task_id, status=status)
            if updated is None:
                raise KeyError(task_id)
        else:
            task = self.get_task(task_id)
            updated_at = _utc_now_iso()
            updated = replace(task, status=status, updated_at=updated_at)
            self._tasks[task_id] = updated

        self._outputs[task_id] = TaskOutputRecord(
            task_id=task_id,
            status=status,
            content=f"Task status changed to {status}",
            updated_at=updated.updated_at,
        )
        return updated

    def get_output(self, task_id: str) -> TaskOutputRecord | None:
        return self._outputs.get(task_id)
