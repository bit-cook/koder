"""File-based task persistence under ~/.koder/tasks/."""

from __future__ import annotations

import fcntl
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import TaskRecord, TaskStatus


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStorage:
    """JSON-file-per-task storage with file locking.

    Directory layout:
        <root>/
            <id>.json          # One file per task
            .highwatermark     # Max ID ever assigned
    """

    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _hwm_path(self) -> Path:
        return self._root / ".highwatermark"

    def _read_hwm(self) -> int:
        p = self._hwm_path()
        if p.exists():
            return int(p.read_text().strip())
        return 0

    def _write_hwm(self, value: int) -> None:
        self._hwm_path().write_text(str(value))

    def _next_id(self) -> str:
        hwm = self._read_hwm() + 1
        self._write_hwm(hwm)
        return str(hwm)

    def _task_path(self, task_id: str) -> Path:
        return self._root / f"{task_id}.json"

    def _read_task(self, task_id: str) -> TaskRecord | None:
        p = self._task_path(task_id)
        if not p.exists():
            return None
        data = json.loads(p.read_text())
        return TaskRecord.from_dict(data)

    def _write_task(self, task: TaskRecord) -> None:
        p = self._task_path(task.id)
        p.write_text(json.dumps(task.to_dict(), indent=2))

    def _with_lock(self, fn):
        """Execute fn while holding an exclusive lock on the storage dir."""
        lock_path = self._root / ".lock"
        lock_path.touch(exist_ok=True)
        with open(lock_path, "r") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                return fn()
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)

    def create(
        self,
        title: str,
        *,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        def _do():
            task_id = self._next_id()
            task = TaskRecord.create(
                task_id=task_id,
                title=title,
                description=description,
                metadata=metadata,
            )
            self._write_task(task)
            return task

        return self._with_lock(_do)

    def get(self, task_id: str) -> TaskRecord | None:
        return self._read_task(task_id)

    def list_all(self, *, filter_resolved_blockers: bool = False) -> list[TaskRecord]:
        tasks = []
        for p in sorted(self._root.glob("*.json")):
            data = json.loads(p.read_text())
            tasks.append(TaskRecord.from_dict(data))

        if filter_resolved_blockers:
            completed_ids = {t.id for t in tasks if t.status == "completed"}
            filtered = []
            for t in tasks:
                if t.blocked_by:
                    active_blockers = [b for b in t.blocked_by if b not in completed_ids]
                    t = replace(t, blocked_by=active_blockers)
                filtered.append(t)
            tasks = filtered

        return tasks

    def update(
        self,
        task_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: TaskStatus | None = None,
        owner: str | None = ...,
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord | None:
        def _do():
            task = self._read_task(task_id)
            if task is None:
                return None

            changes: dict[str, Any] = {"updated_at": _utc_now_iso()}
            if title is not None:
                changes["title"] = title
            if description is not None:
                changes["description"] = description
            if status is not None:
                changes["status"] = status
            if owner is not ...:
                changes["owner"] = owner
            if metadata is not None:
                merged = dict(task.metadata)
                for k, v in metadata.items():
                    if v is None:
                        merged.pop(k, None)
                    else:
                        merged[k] = v
                changes["metadata"] = merged

            updated = replace(task, **changes)
            self._write_task(updated)
            return updated

        return self._with_lock(_do)

    def delete(self, task_id: str) -> bool:
        def _do():
            p = self._task_path(task_id)
            if not p.exists():
                return False
            hwm = self._read_hwm()
            tid = int(task_id) if task_id.isdigit() else 0
            if tid > hwm:
                self._write_hwm(tid)
            p.unlink()
            for other_path in self._root.glob("*.json"):
                data = json.loads(other_path.read_text())
                changed = False
                if task_id in data.get("blocks", []):
                    data["blocks"].remove(task_id)
                    changed = True
                if task_id in data.get("blocked_by", []):
                    data["blocked_by"].remove(task_id)
                    changed = True
                if changed:
                    other_path.write_text(json.dumps(data, indent=2))
            return True

        return self._with_lock(_do)

    def add_block(self, *, blocker_id: str, blocked_id: str) -> bool:
        def _do():
            blocker = self._read_task(blocker_id)
            blocked = self._read_task(blocked_id)
            if blocker is None or blocked is None:
                return False

            if blocked_id not in blocker.blocks:
                updated_blocker = replace(
                    blocker,
                    blocks=[*blocker.blocks, blocked_id],
                    updated_at=_utc_now_iso(),
                )
                self._write_task(updated_blocker)
            if blocker_id not in blocked.blocked_by:
                updated_blocked = replace(
                    blocked,
                    blocked_by=[*blocked.blocked_by, blocker_id],
                    updated_at=_utc_now_iso(),
                )
                self._write_task(updated_blocked)
            return True

        return self._with_lock(_do)
