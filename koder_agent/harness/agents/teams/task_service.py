"""Filesystem-backed shared task lists for agent teams."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from koder_agent.harness.agents.hooks import dispatch_project_hook_event

from .runtime import default_tasks_root


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


@dataclass(frozen=True)
class TeamTaskRecord:
    """A persisted team task entry."""

    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blocks: list[str]
    blocked_by: list[str]
    active_form: str | None
    metadata: dict[str, Any] | None
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        subject: str,
        description: str = "",
        status: str = "pending",
        owner: str | None = None,
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        active_form: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "TeamTaskRecord":
        timestamp = _utc_now_iso()
        return cls(
            id=task_id,
            subject=subject,
            description=description,
            status=status,
            owner=owner,
            blocks=list(blocks or []),
            blocked_by=list(blocked_by or []),
            active_form=active_form,
            metadata=dict(metadata or {}) or None,
            created_at=timestamp,
            updated_at=timestamp,
        )


@dataclass(frozen=True)
class ClaimTaskResult:
    """Outcome of claiming a team task."""

    success: bool
    reason: str | None = None
    task: TeamTaskRecord | None = None
    blocked_by_tasks: list[str] | None = None
    busy_with_tasks: list[str] | None = None


class TeamTaskService:
    """Source-backed shared task list stored under `~/.koder/tasks/<team>/`."""

    def __init__(
        self,
        team_name: str,
        *,
        root: Path | None = None,
        cwd: str | Path | None = None,
    ):
        self.team_name = team_name
        self.root = (root or default_tasks_root()).expanduser()
        self.cwd = Path(cwd or Path.cwd())
        self.task_dir = self.root / _sanitize(team_name)
        self.lock_path = self.task_dir / ".lock"
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)

    @classmethod
    def for_test(
        cls,
        team_name: str,
        *,
        root: Path,
        cwd: str | Path | None = None,
    ) -> "TeamTaskService":
        return cls(team_name, root=root, cwd=cwd or root)

    def cleanup(self) -> None:
        if self.task_dir.exists():
            shutil.rmtree(self.task_dir)

    def _task_path(self, task_id: str) -> Path:
        return self.task_dir / f"{_sanitize(task_id)}.json"

    def _read_task(self, path: Path) -> TeamTaskRecord | None:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return TeamTaskRecord(**data)

    def _write_task(self, task: TeamTaskRecord) -> TeamTaskRecord:
        self._task_path(task.id).write_text(
            json.dumps(task.__dict__, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return task

    def get_task(self, task_id: str) -> TeamTaskRecord | None:
        return self._read_task(self._task_path(task_id))

    def list_tasks(self) -> list[TeamTaskRecord]:
        tasks: list[TeamTaskRecord] = []
        for path in sorted(self.task_dir.glob("*.json"), key=lambda item: int(item.stem)):
            task = self._read_task(path)
            if task is not None:
                tasks.append(task)
        return tasks

    def create_task(
        self,
        subject: str,
        *,
        description: str = "",
        blocked_by: list[str] | None = None,
        active_form: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TeamTaskRecord:
        with FileLock(str(self.lock_path) + ".filelock", timeout=5):
            existing = self.list_tasks()
            next_id = str(max((int(task.id) for task in existing), default=0) + 1)
            task = TeamTaskRecord.create(
                task_id=next_id,
                subject=subject,
                description=description,
                blocked_by=blocked_by,
                active_form=active_form,
                metadata=metadata,
            )
            self._write_task(task)
        hook_result = dispatch_project_hook_event(
            cwd=self.cwd,
            event_name="TaskCreated",
            match_value=task.id,
            payload={
                "event": "TaskCreated",
                "team_name": self.team_name,
                "task_id": task.id,
                "subject": task.subject,
                "description": task.description,
                "blocked_by": task.blocked_by,
            },
        )
        if getattr(hook_result, "blocked", False):
            self._task_path(task.id).unlink(missing_ok=True)
            raise RuntimeError(hook_result.block_reason or "Task creation blocked by hook")
        return task

    def update_task(
        self,
        task_id: str,
        *,
        subject: str | None = None,
        description: str | None = None,
        status: str | None = None,
        owner: str | None = None,
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        active_form: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TeamTaskRecord:
        with FileLock(str(self.lock_path) + ".filelock", timeout=5):
            existing = self.get_task(task_id)
            if existing is None:
                raise KeyError(task_id)
            updated = TeamTaskRecord(
                id=existing.id,
                subject=subject if subject is not None else existing.subject,
                description=description if description is not None else existing.description,
                status=status if status is not None else existing.status,
                owner=owner if owner is not None else existing.owner,
                blocks=list(blocks if blocks is not None else existing.blocks),
                blocked_by=list(blocked_by if blocked_by is not None else existing.blocked_by),
                active_form=active_form if active_form is not None else existing.active_form,
                metadata=dict(metadata if metadata is not None else (existing.metadata or {}))
                or None,
                created_at=existing.created_at,
                updated_at=_utc_now_iso(),
            )
            self._write_task(updated)
        if existing.status != "completed" and updated.status == "completed":
            hook_result = dispatch_project_hook_event(
                cwd=self.cwd,
                event_name="TaskCompleted",
                match_value=task_id,
                payload={
                    "event": "TaskCompleted",
                    "team_name": self.team_name,
                    "task_id": updated.id,
                    "subject": updated.subject,
                    "owner": updated.owner,
                },
            )
            if getattr(hook_result, "blocked", False):
                self._write_task(existing)
                raise RuntimeError(hook_result.block_reason or "Task completion blocked by hook")
        return updated

    def update_status(self, task_id: str, status: str) -> TeamTaskRecord:
        return self.update_task(task_id, status=status)

    def block_task(self, from_task_id: str, to_task_id: str) -> bool:
        with FileLock(str(self.lock_path) + ".filelock", timeout=5):
            source = self.get_task(from_task_id)
            target = self.get_task(to_task_id)
            if source is None or target is None:
                return False
            if to_task_id not in source.blocks:
                source = TeamTaskRecord(
                    id=source.id,
                    subject=source.subject,
                    description=source.description,
                    status=source.status,
                    owner=source.owner,
                    blocks=[*source.blocks, to_task_id],
                    blocked_by=list(source.blocked_by),
                    active_form=source.active_form,
                    metadata=dict(source.metadata or {}) or None,
                    created_at=source.created_at,
                    updated_at=_utc_now_iso(),
                )
                self._write_task(source)
            if from_task_id not in target.blocked_by:
                target = TeamTaskRecord(
                    id=target.id,
                    subject=target.subject,
                    description=target.description,
                    status=target.status,
                    owner=target.owner,
                    blocks=list(target.blocks),
                    blocked_by=[*target.blocked_by, from_task_id],
                    active_form=target.active_form,
                    metadata=dict(target.metadata or {}) or None,
                    created_at=target.created_at,
                    updated_at=_utc_now_iso(),
                )
                self._write_task(target)
            return source is not None

    def claim_task(
        self,
        task_id: str,
        claimant_agent_id: str,
        *,
        check_agent_busy: bool = False,
    ) -> ClaimTaskResult:
        with FileLock(str(self.lock_path) + ".filelock", timeout=5):
            tasks = self.list_tasks()
            task = next((item for item in tasks if item.id == task_id), None)
            if task is None:
                return ClaimTaskResult(success=False, reason="task_not_found")
            if task.status == "completed":
                return ClaimTaskResult(success=False, reason="already_resolved", task=task)
            if task.owner and task.owner != claimant_agent_id:
                return ClaimTaskResult(success=False, reason="already_claimed", task=task)
            unresolved = {item.id for item in tasks if item.status != "completed"}
            blockers = [item for item in task.blocked_by if item in unresolved]
            if blockers:
                return ClaimTaskResult(
                    success=False,
                    reason="blocked",
                    task=task,
                    blocked_by_tasks=blockers,
                )
            if check_agent_busy:
                busy = [
                    item.id
                    for item in tasks
                    if item.owner == claimant_agent_id
                    and item.id != task_id
                    and item.status != "completed"
                ]
                if busy:
                    return ClaimTaskResult(
                        success=False,
                        reason="agent_busy",
                        task=task,
                        busy_with_tasks=busy,
                    )
            claimed = TeamTaskRecord(
                id=task.id,
                subject=task.subject,
                description=task.description,
                status="in_progress",
                owner=claimant_agent_id,
                blocks=list(task.blocks),
                blocked_by=list(task.blocked_by),
                active_form=task.active_form,
                metadata=dict(task.metadata or {}) or None,
                created_at=task.created_at,
                updated_at=_utc_now_iso(),
            )
            self._write_task(claimed)
            return ClaimTaskResult(success=True, task=claimed)
