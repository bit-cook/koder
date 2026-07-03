"""Per-session goal runtime: turn accounting and continuation decisions.

Koder usage is only observable at turn boundaries (one
``AgentScheduler.handle`` call = one goal turn):

- Token deltas are measured by diffing the cumulative usage-tracker counters
  captured at turn start against turn end.
- Wall-clock time is charged for the duration of each goal turn.
- A goal created mid-turn by the ``create_goal`` tool is not charged for the
  creating turn because Koder cannot split a turn's usage after the fact.
- The budget-limit wrap-up prompt runs as one extra hidden turn instead of a
  mid-turn steering injection.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from .goal_prompts import budget_limit_prompt, continuation_prompt
from .goals import Goal, GoalAccountingMode, GoalStatus, GoalStore

logger = logging.getLogger(__name__)


class GoalRuntime:
    """Tracks goal accounting across scheduler turns for one session."""

    def __init__(self, session_id: str, store: GoalStore):
        self.session_id = session_id
        self.store = store
        self._turn_goal_id: Optional[str] = None
        self._turn_started_at: Optional[float] = None
        self._turn_token_baseline: int = 0
        self._goal_created_this_turn: bool = False
        self._budget_limit_reported_goal_id: Optional[str] = None

    # -- turn lifecycle -------------------------------------------------

    async def on_turn_start(self, cumulative_tokens: int) -> None:
        """Snapshot baselines and bind the current goal (if chargeable)."""
        self._turn_started_at = time.monotonic()
        self._turn_token_baseline = cumulative_tokens
        self._goal_created_this_turn = False
        self._turn_goal_id = None
        try:
            goal = await self.store.get_goal(self.session_id)
        except Exception:
            logger.debug("Failed to read goal at turn start", exc_info=True)
            return
        if goal is not None and goal.status in (GoalStatus.ACTIVE, GoalStatus.BUDGET_LIMITED):
            self._turn_goal_id = goal.goal_id
            if self._budget_limit_reported_goal_id not in (None, goal.goal_id):
                self._budget_limit_reported_goal_id = None

    def mark_goal_created(self, goal_id: str) -> None:
        """Record an in-turn ``create_goal``; the creating turn is not charged."""
        self._turn_goal_id = goal_id
        self._goal_created_this_turn = True
        self._budget_limit_reported_goal_id = None

    def clear_turn_goal(self) -> None:
        """Detach goal accounting from the current turn (terminal tool update)."""
        self._budget_limit_reported_goal_id = None

    async def on_turn_end(
        self,
        cumulative_tokens: int,
        *,
        error: bool = False,
        cancelled: bool = False,
    ) -> Optional[Goal]:
        """Charge the finished turn against the goal and apply stop reasons.

        Returns the post-accounting goal, or ``None`` when no goal was bound to
        this turn.
        """
        goal_id = self._turn_goal_id
        started_at = self._turn_started_at
        self._turn_goal_id = None
        self._turn_started_at = None
        if goal_id is None:
            return None

        time_delta = 0
        if started_at is not None:
            time_delta = max(int(time.monotonic() - started_at), 0)
        token_delta = max(cumulative_tokens - self._turn_token_baseline, 0)
        if self._goal_created_this_turn:
            # Tokens spent before/around goal creation are not chargeable and
            # koder cannot split the turn, so skip the whole creating turn.
            token_delta = 0
            time_delta = 0

        goal = await self._account(goal_id, time_delta, token_delta)

        if cancelled:
            paused = await self._safe_pause()
            return paused or goal
        if error:
            blocked = await self._safe_block(goal_id)
            return blocked or goal
        return goal

    async def _account(self, goal_id: str, time_delta: int, token_delta: int) -> Optional[Goal]:
        try:
            current = await self.store.get_goal(self.session_id)
        except Exception:
            logger.debug("Failed to read goal at turn end", exc_info=True)
            return None
        if current is None or current.goal_id != goal_id:
            return current

        if current.status is GoalStatus.COMPLETE:
            mode = GoalAccountingMode.ACTIVE_OR_COMPLETE
        elif current.status in (GoalStatus.PAUSED, GoalStatus.BLOCKED, GoalStatus.USAGE_LIMITED):
            mode = GoalAccountingMode.ACTIVE_OR_STOPPED
        else:
            mode = GoalAccountingMode.ACTIVE_ONLY

        try:
            outcome = await self.store.account_usage(
                self.session_id,
                time_delta_seconds=time_delta,
                token_delta=token_delta,
                mode=mode,
                expected_goal_id=goal_id,
            )
        except Exception:
            logger.debug("Goal usage accounting failed", exc_info=True)
            return current
        return outcome.goal

    async def _safe_pause(self) -> Optional[Goal]:
        try:
            return await self.store.pause_active_goal(self.session_id)
        except Exception:
            logger.debug("Failed to pause goal after cancellation", exc_info=True)
            return None

    async def _safe_block(self, goal_id: str) -> Optional[Goal]:
        # A terminal turn error blocks the goal so automatic continuation
        # cannot loop and consume tokens.
        from .goals import GoalUpdate

        try:
            return await self.store.update_goal(
                self.session_id,
                GoalUpdate(status=GoalStatus.BLOCKED, expected_goal_id=goal_id),
            )
        except Exception:
            logger.debug("Failed to block goal after turn error", exc_info=True)
            return None

    # -- continuation decisions -----------------------------------------

    async def next_continuation_prompt(self) -> Optional[str]:
        """Return the hidden prompt for the next automatic goal turn.

        ``None`` means the loop should stop: no goal, or the goal is in a
        paused/terminal state with nothing left to report.
        """
        try:
            goal = await self.store.get_goal(self.session_id)
        except Exception:
            logger.debug("Failed to read goal for continuation", exc_info=True)
            return None
        if goal is None:
            return None
        if goal.status is GoalStatus.ACTIVE:
            return continuation_prompt(goal)
        if goal.status is GoalStatus.BUDGET_LIMITED:
            if self._budget_limit_reported_goal_id == goal.goal_id:
                return None
            self._budget_limit_reported_goal_id = goal.goal_id
            return budget_limit_prompt(goal)
        return None


__all__ = ["GoalRuntime"]
