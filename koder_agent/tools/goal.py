"""Goal tools: get_goal, create_goal, update_goal.

The scheduler publishes the active session's
:class:`~koder_agent.core.goal_runtime.GoalRuntime` into a context variable
before invoking the runner (the same pattern as ``tools/permission_context.py``),
so the tools can read and mutate the goal for the correct session.
"""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Optional

from .compat import function_tool

if TYPE_CHECKING:
    from koder_agent.core.goal_runtime import GoalRuntime

_goal_runtime_var: ContextVar[Optional["GoalRuntime"]] = ContextVar(
    "koder_goal_runtime", default=None
)

COMPLETION_BUDGET_REPORT = (
    "Goal achieved. Report final usage from this tool result's structured goal "
    "fields. If `goal.tokenBudget` is present, include token usage from "
    "`goal.tokensUsed` and `goal.tokenBudget`. If `goal.timeUsedSeconds` is "
    "greater than 0, summarize elapsed time in a concise, human-friendly form "
    "appropriate to the response language."
)


def set_goal_context(runtime: Optional["GoalRuntime"]) -> Token:
    """Publish the active session's goal runtime for tool invocations."""
    return _goal_runtime_var.set(runtime)


def reset_goal_context(token: Token) -> None:
    _goal_runtime_var.reset(token)


def get_goal_runtime() -> Optional["GoalRuntime"]:
    return _goal_runtime_var.get()


def _goal_payload(goal) -> dict:
    """Serialize a goal in the camelCase tool-result shape."""
    payload = {
        "threadId": goal.session_id,
        "objective": goal.objective,
        "status": goal.status.value,
        "tokensUsed": goal.tokens_used,
        "timeUsedSeconds": goal.time_used_seconds,
        "createdAt": goal.created_at_ms // 1000,
        "updatedAt": goal.updated_at_ms // 1000,
    }
    if goal.token_budget is not None:
        payload["tokenBudget"] = goal.token_budget
    return payload


def _goal_response(goal, *, include_completion_budget_report: bool = False) -> str:
    from koder_agent.core.goals import GoalStatus

    remaining = None
    if goal is not None and goal.token_budget is not None:
        remaining = max(goal.token_budget - goal.tokens_used, 0)
    completion_budget_report = None
    if (
        include_completion_budget_report
        and goal is not None
        and goal.status is GoalStatus.COMPLETE
        and (goal.token_budget is not None or goal.time_used_seconds > 0)
    ):
        completion_budget_report = COMPLETION_BUDGET_REPORT
    return json.dumps(
        {
            "goal": _goal_payload(goal) if goal is not None else None,
            "remainingTokens": remaining,
            "completionBudgetReport": completion_budget_report,
        },
        ensure_ascii=False,
    )


@function_tool
async def get_goal() -> str:
    """Get the current goal for this thread, including status, budgets, token and elapsed-time usage, and remaining token budget."""
    runtime = get_goal_runtime()
    if runtime is None:
        return "failed to read goal: goals are not available in this session"
    try:
        goal = await runtime.store.get_goal(runtime.session_id)
    except Exception as exc:
        return f"failed to read goal: {exc}"
    return _goal_response(goal)


@function_tool
async def create_goal(objective: str, token_budget: Optional[int] = None) -> str:
    """Create a goal only when explicitly requested by the user or system/developer instructions; do not infer goals from ordinary tasks.

    Set token_budget only when an explicit token budget is requested. Fails if an
    unfinished goal exists; use update_goal only for status.

    Args:
        objective: Required. The concrete objective to start pursuing. This starts
            a new active goal when no goal exists or replaces the current goal when
            it is complete.
        token_budget: Positive token budget for the new goal. Omit unless
            explicitly requested.
    """
    from koder_agent.core.goals import (
        GoalStatus,
        validate_goal_budget,
        validate_goal_objective,
    )

    runtime = get_goal_runtime()
    if runtime is None:
        return "failed to create goal: goals are not available in this session"

    objective = objective.strip()
    try:
        validate_goal_objective(objective)
        validate_goal_budget(token_budget)
    except ValueError as exc:
        return str(exc)

    try:
        goal = await runtime.store.insert_goal(
            runtime.session_id,
            objective,
            GoalStatus.ACTIVE,
            token_budget,
        )
    except Exception as exc:
        return f"failed to create goal: {exc}"
    if goal is None:
        return (
            "cannot create a new goal because this thread has an unfinished goal; "
            "complete the existing goal first"
        )
    runtime.mark_goal_created(goal.goal_id)
    return _goal_response(goal)


@function_tool
async def update_goal(status: str) -> str:
    """Update the existing goal.

    Use this tool only to mark the goal achieved or genuinely blocked.
    Set status to `complete` only when the objective has actually been achieved and no required work remains.
    Set status to `blocked` only when the same blocking condition has repeated for at least three consecutive goal turns, counting the original/user-triggered turn and any automatic continuations, and the agent cannot make meaningful progress without user input or an external-state change.
    If the user resumes a goal that was previously marked `blocked`, treat the resumed run as a fresh blocked audit. If the same blocking condition then repeats for at least three consecutive resumed goal turns, set status to `blocked` again.
    Once the blocked threshold is satisfied, do not keep reporting that you are still blocked while leaving the goal active; set status to `blocked`.
    Do not use `blocked` merely because the work is hard, slow, uncertain, incomplete, or would benefit from clarification.
    Do not mark a goal complete merely because its budget is nearly exhausted or because you are stopping work.
    You cannot use this tool to pause, resume, budget-limit, or usage-limit a goal; those status changes are controlled by the user or system.
    When marking a budgeted goal achieved with status `complete`, report the final token usage from the tool result to the user.

    Args:
        status: Required. Set to `complete` only when the objective is achieved and
            no required work remains. Set to `blocked` only after the same blocking
            condition has recurred for at least three consecutive goal turns and
            the agent is at an impasse. After a previously blocked goal is resumed,
            the resumed run starts a fresh blocked audit.
    """
    from koder_agent.core.goals import GoalStatus, GoalUpdate

    runtime = get_goal_runtime()
    if runtime is None:
        return "failed to update goal: goals are not available in this session"

    try:
        requested = GoalStatus.from_str(status)
    except ValueError as exc:
        return str(exc)
    if requested not in (GoalStatus.COMPLETE, GoalStatus.BLOCKED):
        return (
            "update_goal can only mark the existing goal complete or blocked; "
            "pause, resume, budget-limited, and usage-limited status changes are "
            "controlled by the user or system"
        )

    try:
        goal = await runtime.store.update_goal(
            runtime.session_id,
            GoalUpdate(status=requested),
        )
    except Exception as exc:
        return f"failed to update goal: {exc}"
    if goal is None:
        return "cannot update goal because this thread has no goal"
    runtime.clear_turn_goal()
    return _goal_response(
        goal,
        include_completion_budget_report=requested is GoalStatus.COMPLETE,
    )


__all__ = [
    "COMPLETION_BUDGET_REPORT",
    "create_goal",
    "get_goal",
    "get_goal_runtime",
    "reset_goal_context",
    "set_goal_context",
    "update_goal",
]
