"""Display helpers for session goals."""

from __future__ import annotations

from .goals import Goal, GoalStatus

GOAL_USAGE = "Usage: /goal [<objective>|clear|edit <objective>|pause|resume|budget <tokens>]"
GOAL_USAGE_HINT = "Example: /goal improve benchmark coverage"


def format_tokens_compact(value: int) -> str:
    """Compact token count: 999 -> "999", 12_500 -> "12.5K", 40_000 -> "40K"."""
    value = max(value, 0)
    if value == 0:
        return "0"
    if value < 1_000:
        return str(value)

    if value >= 1_000_000_000_000:
        scaled, suffix = value / 1_000_000_000_000, "T"
    elif value >= 1_000_000_000:
        scaled, suffix = value / 1_000_000_000, "B"
    elif value >= 1_000_000:
        scaled, suffix = value / 1_000_000, "M"
    else:
        scaled, suffix = value / 1_000, "K"

    if scaled < 10.0:
        decimals = 2
    elif scaled < 100.0:
        decimals = 1
    else:
        decimals = 0
    text = f"{scaled:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}{suffix}"


def format_goal_elapsed_seconds(seconds: int) -> str:
    """Compact elapsed time: 59 -> "59s", 90*60 -> "1h 30m", 1d -> "1d 0h 0m"."""
    seconds = max(seconds, 0)
    if seconds < 60:
        return f"{seconds}s"

    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"

    hours = minutes // 60
    remaining_minutes = minutes % 60
    if hours >= 24:
        days = hours // 24
        remaining_hours = hours % 24
        return f"{days}d {remaining_hours}h {remaining_minutes}m"

    if remaining_minutes == 0:
        return f"{hours}h"
    return f"{hours}h {remaining_minutes}m"


def goal_status_label(status: GoalStatus) -> str:
    return {
        GoalStatus.ACTIVE: "active",
        GoalStatus.PAUSED: "paused",
        GoalStatus.BLOCKED: "blocked",
        GoalStatus.USAGE_LIMITED: "usage limited",
        GoalStatus.BUDGET_LIMITED: "limited by budget",
        GoalStatus.COMPLETE: "complete",
    }[status]


def goal_usage_summary(goal: Goal) -> str:
    parts = [f"Objective: {goal.objective}"]
    if goal.time_used_seconds > 0:
        parts.append(f"Time: {format_goal_elapsed_seconds(goal.time_used_seconds)}.")
    if goal.token_budget is not None:
        parts.append(
            f"Tokens: {format_tokens_compact(goal.tokens_used)}"
            f"/{format_tokens_compact(goal.token_budget)}."
        )
    return " ".join(parts)


def goal_summary_text(goal: Goal) -> str:
    """Multi-line summary shown by the bare ``/goal`` command."""
    lines = [
        "Goal",
        f"Status: {goal_status_label(goal.status)}",
        f"Objective: {goal.objective}",
        f"Time used: {format_goal_elapsed_seconds(goal.time_used_seconds)}",
        f"Tokens used: {format_tokens_compact(goal.tokens_used)}",
    ]
    if goal.token_budget is not None:
        lines.append(f"Token budget: {format_tokens_compact(goal.token_budget)}")
    if goal.status is GoalStatus.ACTIVE:
        hint = "Commands: /goal edit, /goal pause, /goal clear"
    elif goal.status in (GoalStatus.PAUSED, GoalStatus.BLOCKED, GoalStatus.USAGE_LIMITED):
        hint = "Commands: /goal edit, /goal resume, /goal clear"
    else:
        hint = "Commands: /goal edit, /goal clear"
    lines.append("")
    lines.append(hint)
    return "\n".join(lines)


def should_confirm_before_replacing_goal(goal: Goal) -> bool:
    """Completed goals are terminal; replacing them needs no confirmation."""
    return goal.status is not GoalStatus.COMPLETE


__all__ = [
    "GOAL_USAGE",
    "GOAL_USAGE_HINT",
    "format_goal_elapsed_seconds",
    "format_tokens_compact",
    "goal_status_label",
    "goal_summary_text",
    "goal_usage_summary",
    "should_confirm_before_replacing_goal",
]
