"""Goal display helper tests."""

from koder_agent.core.goal_display import (
    GOAL_USAGE,
    format_goal_elapsed_seconds,
    format_tokens_compact,
    goal_status_label,
    goal_summary_text,
    goal_usage_summary,
    should_confirm_before_replacing_goal,
)
from koder_agent.core.goals import Goal, GoalStatus


def make_goal(
    status: GoalStatus = GoalStatus.BUDGET_LIMITED,
    token_budget=None,
    tokens_used: int = 0,
    time_used_seconds: int = 120,
    objective: str = "Complete the task described in ../gameboy-long-running-prompt5.txt",
) -> Goal:
    return Goal(
        session_id="thread-1",
        goal_id="goal-1",
        objective=objective,
        status=status,
        token_budget=token_budget,
        tokens_used=tokens_used,
        time_used_seconds=time_used_seconds,
        created_at_ms=0,
        updated_at_ms=0,
    )


def test_format_goal_elapsed_seconds_is_compact():
    assert format_goal_elapsed_seconds(0) == "0s"
    assert format_goal_elapsed_seconds(59) == "59s"
    assert format_goal_elapsed_seconds(60) == "1m"
    assert format_goal_elapsed_seconds(30 * 60) == "30m"
    assert format_goal_elapsed_seconds(90 * 60) == "1h 30m"
    assert format_goal_elapsed_seconds(2 * 60 * 60) == "2h"
    assert format_goal_elapsed_seconds(24 * 60 * 60 - 1) == "23h 59m"
    assert format_goal_elapsed_seconds(24 * 60 * 60) == "1d 0h 0m"
    assert format_goal_elapsed_seconds(2 * 24 * 60 * 60 + 23 * 60 * 60 + 42 * 60) == "2d 23h 42m"
    assert format_goal_elapsed_seconds(-5) == "0s"


def test_format_tokens_compact():
    assert format_tokens_compact(0) == "0"
    assert format_tokens_compact(999) == "999"
    assert format_tokens_compact(12_500) == "12.5K"
    assert format_tokens_compact(50_000) == "50K"
    assert format_tokens_compact(63_876) == "63.9K"
    assert format_tokens_compact(40_000) == "40K"
    assert format_tokens_compact(1_500_000) == "1.5M"
    assert format_tokens_compact(-7) == "0"


def test_goal_status_labels():
    assert goal_status_label(GoalStatus.ACTIVE) == "active"
    assert goal_status_label(GoalStatus.PAUSED) == "paused"
    assert goal_status_label(GoalStatus.BLOCKED) == "blocked"
    assert goal_status_label(GoalStatus.USAGE_LIMITED) == "usage limited"
    assert goal_status_label(GoalStatus.BUDGET_LIMITED) == "limited by budget"
    assert goal_status_label(GoalStatus.COMPLETE) == "complete"


def test_goal_usage_summary_formats_time_and_budgeted_tokens():
    summary = goal_usage_summary(make_goal(token_budget=50_000, tokens_used=63_876))
    assert summary == (
        "Objective: Complete the task described in ../gameboy-long-running-prompt5.txt "
        "Time: 2m. Tokens: 63.9K/50K."
    )


def test_goal_usage_summary_omits_zero_time_and_missing_budget():
    summary = goal_usage_summary(make_goal(time_used_seconds=0, objective="Do it"))
    assert summary == "Objective: Do it"


def test_goal_summary_text_shows_budget_and_command_hints():
    text = goal_summary_text(
        make_goal(status=GoalStatus.ACTIVE, token_budget=50_000, tokens_used=12_500)
    )
    assert "Status: active" in text
    assert "Tokens used: 12.5K" in text
    assert "Token budget: 50K" in text
    assert "Commands: /goal edit, /goal pause, /goal clear" in text


def test_goal_summary_text_command_hints_per_status():
    for status in (GoalStatus.PAUSED, GoalStatus.BLOCKED, GoalStatus.USAGE_LIMITED):
        text = goal_summary_text(make_goal(status=status))
        assert "Commands: /goal edit, /goal resume, /goal clear" in text
    for status in (GoalStatus.BUDGET_LIMITED, GoalStatus.COMPLETE):
        text = goal_summary_text(make_goal(status=status))
        assert "Commands: /goal edit, /goal clear" in text


def test_completed_goal_does_not_require_replace_confirmation():
    assert not should_confirm_before_replacing_goal(make_goal(status=GoalStatus.COMPLETE))


def test_unfinished_goals_require_replace_confirmation():
    for status in (
        GoalStatus.ACTIVE,
        GoalStatus.PAUSED,
        GoalStatus.BLOCKED,
        GoalStatus.USAGE_LIMITED,
        GoalStatus.BUDGET_LIMITED,
    ):
        assert should_confirm_before_replacing_goal(make_goal(status=status))


def test_goal_usage_constant_mentions_subcommands():
    for keyword in ("clear", "edit", "pause", "resume"):
        assert keyword in GOAL_USAGE
