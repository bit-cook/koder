"""Worktree lifecycle primitives for the harness runtime."""

from .conflicts import ConflictResult, detect_conflict
from .service import WorktreeCreateResult, WorktreeService, WorktreeTransitionResult

__all__ = [
    "ConflictResult",
    "WorktreeCreateResult",
    "WorktreeService",
    "WorktreeTransitionResult",
    "detect_conflict",
]
