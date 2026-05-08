"""Tests for system prompt completeness."""

from koder_agent.utils.prompts import KODER_SYSTEM_PROMPT


def test_prompt_has_system_section():
    assert "# System" in KODER_SYSTEM_PROMPT


def test_prompt_has_doing_tasks_section():
    assert "# Doing tasks" in KODER_SYSTEM_PROMPT


def test_prompt_has_executing_actions_section():
    assert "# Executing actions with care" in KODER_SYSTEM_PROMPT


def test_prompt_has_tools_section():
    assert "# Using your tools" in KODER_SYSTEM_PROMPT


def test_prompt_has_tone_section():
    assert "# Tone and style" in KODER_SYSTEM_PROMPT


def test_prompt_has_output_efficiency():
    assert "# Output efficiency" in KODER_SYSTEM_PROMPT


def test_prompt_has_session_guidance():
    assert "# Session-specific guidance" in KODER_SYSTEM_PROMPT


def test_prompt_has_environment_section():
    assert "# Environment" in KODER_SYSTEM_PROMPT


def test_prompt_has_committing_section():
    assert "# Committing changes with git" in KODER_SYSTEM_PROMPT


def test_prompt_has_creating_prs_section():
    assert "# Creating pull requests" in KODER_SYSTEM_PROMPT


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
