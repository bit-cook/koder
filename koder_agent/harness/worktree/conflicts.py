"""Conflict detection for worktree/session transitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConflictResult:
    """Conflict result for orchestration transitions."""

    blocked: bool
    reason: str


def detect_conflict(
    *,
    active_task_id: str,
    requested_task_id: str,
    active_session_id: str,
    requested_session_id: str,
) -> ConflictResult:
    if active_task_id != requested_task_id and active_session_id != requested_session_id:
        return ConflictResult(blocked=True, reason="task and session ownership conflict")
    return ConflictResult(blocked=False, reason="no conflict")
