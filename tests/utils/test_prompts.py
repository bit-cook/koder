"""Tests for system prompt completeness."""

from koder_agent.utils.prompts import KODER_SYSTEM_PROMPT


def test_prompt_has_harness_section():
    assert "# Harness" in KODER_SYSTEM_PROMPT


def test_prompt_has_communicating_section():
    assert "# Communicating with the user" in KODER_SYSTEM_PROMPT


def test_prompt_has_doing_tasks_section():
    assert "# Doing tasks" in KODER_SYSTEM_PROMPT


def test_prompt_has_executing_actions_section():
    assert "# Executing actions with care" in KODER_SYSTEM_PROMPT


def test_prompt_has_tools_section():
    assert "# Using your tools" in KODER_SYSTEM_PROMPT


def test_prompt_has_plan_mode_section():
    assert "# Plan mode" in KODER_SYSTEM_PROMPT


def test_prompt_has_memory_section():
    assert "# Memory" in KODER_SYSTEM_PROMPT


def test_prompt_has_session_guidance():
    assert "# Session-specific guidance" in KODER_SYSTEM_PROMPT


def test_prompt_has_environment_section():
    assert "# Environment" in KODER_SYSTEM_PROMPT


def test_prompt_has_committing_section():
    assert "# Committing changes with git" in KODER_SYSTEM_PROMPT


def test_prompt_has_creating_prs_section():
    assert "# Creating pull requests" in KODER_SYSTEM_PROMPT


def test_prompt_has_git_safety_protocol():
    assert "Git Safety Protocol" in KODER_SYSTEM_PROMPT
    # The amend trap: a failed pre-commit hook means the commit never happened.
    assert "pre-commit hook fails" in KODER_SYSTEM_PROMPT
    assert "--amend" in KODER_SYSTEM_PROMPT


def test_prompt_has_denial_adjustment_doctrine():
    """A denied tool call means the user declined — adjust, don't retry."""
    assert "denied tool call means the user declined" in KODER_SYSTEM_PROMPT
    assert "do not retry the same call verbatim" in KODER_SYSTEM_PROMPT


def test_prompt_has_readable_over_concise_doctrine():
    assert "teammate who stepped away" in KODER_SYSTEM_PROMPT
    assert "readable matters more" in KODER_SYSTEM_PROMPT
    assert "Lead with the outcome" in KODER_SYSTEM_PROMPT


def test_prompt_has_final_message_completeness():
    assert "final text message of your turn" in KODER_SYSTEM_PROMPT


def test_prompt_has_verification_doctrine():
    assert "not feature correctness" in KODER_SYSTEM_PROMPT
    assert "Report outcomes faithfully" in KODER_SYSTEM_PROMPT


def test_prompt_has_subagent_doctrine():
    assert "Never delegate understanding" in KODER_SYSTEM_PROMPT
    assert "smart colleague who just walked into the room" in KODER_SYSTEM_PROMPT


def test_prompt_has_comment_policy():
    assert "constraint the code itself can't show" in KODER_SYSTEM_PROMPT


def test_prompt_has_security_policy_tiers():
    assert "authorized security testing" in KODER_SYSTEM_PROMPT
    assert "clear authorization context" in KODER_SYSTEM_PROMPT


def test_prompt_has_prompt_injection_guidance():
    assert "prompt injection" in KODER_SYSTEM_PROMPT


def test_prompt_has_compaction_reassurance():
    assert "you do not need to wrap up early" in KODER_SYSTEM_PROMPT


def test_prompt_has_koder_branding_only():
    """Ensure prompt keeps Koder as the local product identity."""
    assert "You are Koder" in KODER_SYSTEM_PROMPT
    assert "Anthropic's official CLI" not in KODER_SYSTEM_PROMPT


def test_prompt_has_koder_branding():
    assert "Koder" in KODER_SYSTEM_PROMPT


def test_prompt_has_skills_placeholder():
    assert "{SKILLS_METADATA}" in KODER_SYSTEM_PROMPT


def test_prompt_has_agents_placeholder():
    assert "{AGENTS_METADATA}" in KODER_SYSTEM_PROMPT


def test_prompt_minimum_length():
    """Prompt should be substantial."""
    assert len(KODER_SYSTEM_PROMPT) >= 3000
