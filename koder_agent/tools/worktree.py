"""EnterWorktree and ExitWorktree tools."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents import function_tool

# --- Slug validation ---

_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _validate_slug(slug: str) -> str | None:
    """Validate worktree slug. Returns error message or None."""
    if len(slug) > 64:
        return "Slug must be at most 64 characters"
    if slug.startswith("/") or slug.endswith("/"):
        return "Slug must not start or end with /"
    for segment in slug.split("/"):
        if segment in (".", ".."):
            return f"Invalid segment: {segment}"
        if not _SEGMENT_RE.match(segment):
            return f"Invalid characters in segment: {segment}"
    return None


# --- Session state ---


@dataclass
class WorktreeSession:
    original_cwd: str
    worktree_path: str
    worktree_branch: str
    name: str


_session: WorktreeSession | None = None


def _get_worktree_session() -> WorktreeSession | None:
    return _session


def _set_worktree_session(session: WorktreeSession | None) -> None:
    global _session
    _session = session


# --- Git helpers ---


def _git_root() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


# --- Plain implementations ---


def enter_worktree(name: Optional[str] = None) -> str:
    """Create and enter an isolated git worktree for parallel development."""
    global _session
    if _session is not None:
        return json.dumps(
            {
                "message": f"Already in a worktree session at {_session.worktree_path}. "
                "Exit the current worktree first.",
            }
        )

    if name is None:
        import uuid

        name = f"wt-{uuid.uuid4().hex[:8]}"

    error = _validate_slug(name)
    if error:
        return json.dumps({"message": f"Invalid worktree name: {error}"})

    git_root = _git_root()
    if git_root is None:
        return json.dumps({"message": "Not in a git repository. Cannot create worktree."})

    branch_name = f"worktree-{name.replace('/', '+')}"
    worktree_path = git_root / ".koder" / "worktrees" / name

    try:
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        if not worktree_path.exists():
            subprocess.run(
                [
                    "git",
                    "worktree",
                    "add",
                    "-B",
                    branch_name,
                    str(worktree_path),
                    "HEAD",
                ],
                cwd=git_root,
                check=True,
                capture_output=True,
                text=True,
            )
    except subprocess.CalledProcessError as e:
        return json.dumps({"message": f"Failed to create worktree: {e.stderr.strip()}"})

    _session = WorktreeSession(
        original_cwd=str(os.getcwd()),
        worktree_path=str(worktree_path),
        worktree_branch=branch_name,
        name=name,
    )

    return json.dumps(
        {
            "worktree_path": str(worktree_path),
            "worktree_branch": branch_name,
            "message": f"Created and entered worktree at {worktree_path}",
        }
    )


def exit_worktree(action: str, discard_changes: Optional[bool] = None) -> str:
    """Exit the current worktree session."""
    global _session
    if _session is None:
        return json.dumps(
            {
                "message": "No active worktree session. This tool only operates on "
                "worktrees created by enter_worktree in the current session.",
            }
        )

    session = _session
    result: dict = {
        "action": action,
        "original_cwd": session.original_cwd,
        "worktree_path": session.worktree_path,
        "worktree_branch": session.worktree_branch,
    }

    if action == "remove":
        git_root = _git_root() or Path(session.original_cwd)
        try:
            subprocess.run(
                [
                    "git",
                    "worktree",
                    "remove",
                    "--force",
                    session.worktree_path,
                ],
                cwd=git_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            pass

        try:
            subprocess.run(
                ["git", "branch", "-D", session.worktree_branch],
                cwd=git_root,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            pass

    # Clear session state
    _session = None

    result["message"] = f"Exited worktree. Action: {action}." + (
        f" Worktree kept at {session.worktree_path}" if action == "keep" else " Worktree removed."
    )
    return json.dumps(result)


# --- @function_tool wrappers ---

enter_worktree_tool = function_tool(enter_worktree)
exit_worktree_tool = function_tool(exit_worktree)
