"""Test-scoped worktree service."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from koder_agent.harness.hooks.runtime import dispatch_command_hooks


@dataclass(frozen=True)
class WorktreeCreateResult:
    """Result of creating a worktree."""

    path: Path
    branch: str
    repo_root: Path | None = None


@dataclass(frozen=True)
class WorktreeTransitionResult:
    """Result of entering or exiting a worktree."""

    ok: bool
    path: Path


class WorktreeService:
    """Creates and tracks test-scoped worktree directories."""

    def __init__(self, root: Path, *, repo_root: Path | None = None):
        self.root = root
        self.repo_root = repo_root
        self._active: set[Path] = set()

    @classmethod
    def for_test(cls, root: Path) -> "WorktreeService":
        root.mkdir(parents=True, exist_ok=True)
        repo_root = root if (root / ".git").exists() else None
        worktree_root = root / ".koder" / "worktrees" if repo_root else root
        worktree_root.mkdir(parents=True, exist_ok=True)
        return cls(worktree_root, repo_root=repo_root)

    def create(self, branch: str) -> WorktreeCreateResult:
        branch_slug = branch.replace("/", "-")
        path = self.root / branch_slug
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.repo_root and (self.repo_root / ".git").exists():
            if not path.exists():
                subprocess.run(
                    [
                        "git",
                        "worktree",
                        "add",
                        "-B",
                        branch,
                        str(path),
                        "HEAD",
                    ],
                    cwd=self.repo_root,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            hook_result = dispatch_command_hooks(
                cwd=self.repo_root or path,
                event_name="WorktreeCreate",
                match_value=None,
                payload={
                    "event": "WorktreeCreate",
                    "branch": branch,
                    "worktree_path": str(path),
                },
            )
            if hook_result.worktree_path:
                path = Path(hook_result.worktree_path)
            return WorktreeCreateResult(path=path, branch=branch, repo_root=self.repo_root)

        path.mkdir(parents=True, exist_ok=True)
        hook_result = dispatch_command_hooks(
            cwd=self.repo_root or path,
            event_name="WorktreeCreate",
            match_value=None,
            payload={
                "event": "WorktreeCreate",
                "branch": branch,
                "worktree_path": str(path),
            },
        )
        if hook_result.worktree_path:
            path = Path(hook_result.worktree_path)
        return WorktreeCreateResult(path=path, branch=branch, repo_root=self.repo_root)

    def enter(self, path: Path) -> WorktreeTransitionResult:
        self._active.add(path)
        return WorktreeTransitionResult(ok=True, path=path)

    def exit(self, path: Path) -> WorktreeTransitionResult:
        self._active.discard(path)
        dispatch_command_hooks(
            cwd=self.repo_root or path,
            event_name="WorktreeRemove",
            match_value=None,
            payload={
                "event": "WorktreeRemove",
                "worktree_path": str(path),
            },
        )
        return WorktreeTransitionResult(ok=True, path=path)
