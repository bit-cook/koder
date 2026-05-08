"""PR review status polling for interactive mode status line.

Fetches PR review state via ``gh pr view`` and polls on a background loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PrReviewState = Literal[
    "approved",
    "pending",
    "changes_requested",
    "draft",
    "merged",
]

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

GH_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class PrStatus:
    """Snapshot of a pull-request's review state."""

    number: int
    url: str
    review_state: PrReviewState
    last_updated: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_review_state(is_draft: bool, review_decision: str) -> PrReviewState:
    """Map GitHub API values to a canonical review state.

    Draft PRs always show as ``'draft'`` regardless of *review_decision*.
    *review_decision* can be ``APPROVED``, ``CHANGES_REQUESTED``,
    ``REVIEW_REQUIRED``, or an empty string.
    """
    if is_draft:
        return "draft"
    if review_decision == "APPROVED":
        return "approved"
    if review_decision == "CHANGES_REQUESTED":
        return "changes_requested"
    return "pending"


async def _run_gh(*args: str, timeout: float = GH_TIMEOUT_S) -> Optional[str]:
    """Run a ``gh`` CLI command and return stdout, or *None* on failure.

    Uses ``asyncio.create_subprocess_exec`` (no shell) for safety.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            return None
        return stdout.decode().strip() if stdout else None
    except FileNotFoundError:
        # ``gh`` is not installed.
        return None
    except asyncio.TimeoutError:
        try:
            proc.kill()  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return None
    except Exception:
        return None


async def _get_current_branch(timeout: float = GH_TIMEOUT_S) -> Optional[str]:
    """Return the current git branch name, or *None* on failure.

    Uses ``asyncio.create_subprocess_exec`` (no shell) for safety.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0 or not stdout:
            return None
        return stdout.decode().strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public fetch
# ---------------------------------------------------------------------------


async def fetch_pr_status() -> Optional[PrStatus]:
    """Fetch PR status for the current branch via ``gh pr view``.

    Returns *None* on any failure (``gh`` not installed, not a git repo,
    no PR open, on default branch, PR already merged/closed, etc.).
    """

    # 1. Quick gate: is this even a git repo?  Also grab default branch.
    git_check = await _run_gh("repo", "view", "--json", "defaultBranchRef")
    if git_check is None:
        return None

    try:
        repo_data = json.loads(git_check)
        default_branch: str = repo_data.get("defaultBranchRef", {}).get("name", "main")
    except (json.JSONDecodeError, AttributeError):
        default_branch = "main"

    # 2. Determine current branch (plain git, faster than gh).
    current_branch = await _get_current_branch()
    if not current_branch or current_branch == default_branch:
        return None

    # 3. Fetch PR data.
    pr_stdout = await _run_gh(
        "pr",
        "view",
        "--json",
        "number,url,reviewDecision,isDraft,headRefName,state",
    )
    if not pr_stdout:
        return None

    try:
        data = json.loads(pr_stdout)
    except json.JSONDecodeError:
        return None

    # Filter out PRs from the default branch (edge case: PR from main->other).
    head_ref: str = data.get("headRefName", "")
    if head_ref in (default_branch, "main", "master"):
        return None

    # Filter out merged / closed PRs.
    state: str = data.get("state", "")
    if state in ("MERGED", "CLOSED"):
        return None

    review_state = _derive_review_state(
        is_draft=bool(data.get("isDraft", False)),
        review_decision=data.get("reviewDecision", ""),
    )

    return PrStatus(
        number=int(data.get("number", 0)),
        url=str(data.get("url", "")),
        review_state=review_state,
        last_updated=time.monotonic(),
    )


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------


def pr_status_color(state: str) -> str:
    """Return the display color name for a PR review state.

    Maps to the documented colors from the interactive-mode page:
    green=approved, yellow=pending, red=changes_requested, gray=draft, purple=merged.
    """
    return {
        "approved": "green",
        "pending": "yellow",
        "changes_requested": "red",
        "draft": "gray",
        "merged": "purple",
    }.get(state, "gray")


class PrStatusPoller:
    """Background poller that refreshes PR review status on an interval.

    Parameters
    ----------
    poll_interval:
        Seconds between polls (default 60).
    idle_cutoff:
        Seconds of inactivity before polling stops (default 3600 = 1 hour).
    slow_threshold:
        If any single fetch takes longer than this many seconds the poller
        permanently disables itself (default 4).
    """

    def __init__(
        self,
        poll_interval: float = 60.0,
        idle_cutoff: float = 3600.0,
        slow_threshold: float = 4.0,
    ) -> None:
        self._poll_interval = poll_interval
        self._idle_cutoff = idle_cutoff
        self._slow_threshold = slow_threshold

        self._status: Optional[PrStatus] = None
        self._task: Optional[asyncio.Task[None]] = None
        self._last_interaction: float = time.monotonic()
        self._disabled: bool = False

    # -- public API ---------------------------------------------------------

    def start(self) -> None:
        """Begin background polling.  Safe to call multiple times."""
        if self._task is not None and not self._task.done():
            return
        if self._disabled:
            return
        self._task = asyncio.create_task(self._poll_loop())

    def stop(self) -> None:
        """Cancel the background polling task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def get_status(self) -> Optional[PrStatus]:
        """Return the most recently cached PR status, or *None*."""
        return self._status

    def touch(self) -> None:
        """Mark user activity so the idle timer resets."""
        self._last_interaction = time.monotonic()

    # -- internals ----------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Run indefinitely until cancelled or idle/slow threshold hit."""
        try:
            while True:
                # Idle check.
                if time.monotonic() - self._last_interaction > self._idle_cutoff:
                    logger.debug("PrStatusPoller: idle cutoff reached, stopping.")
                    return

                start = time.monotonic()
                try:
                    result = await fetch_pr_status()
                except Exception:
                    result = None
                elapsed = time.monotonic() - start

                # Slow-threshold permanent disable.
                if elapsed > self._slow_threshold:
                    logger.debug(
                        "PrStatusPoller: fetch took %.1fs (> %.1fs), disabling.",
                        elapsed,
                        self._slow_threshold,
                    )
                    self._disabled = True
                    return

                if result is not None:
                    self._status = result
                else:
                    # Clear stale status when there is no longer an open PR.
                    self._status = None

                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            return
