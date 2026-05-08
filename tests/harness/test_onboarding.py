"""Tests for onboarding wizard state management."""

import os
from unittest.mock import patch

from koder_agent.harness.onboarding import (
    OnboardingState,
    check_onboarding_state,
    get_onboarding_steps,
    is_onboarding_complete,
    mark_onboarding_complete,
)


def test_default_onboarding_state_has_all_false():
    """Test that default OnboardingState has all fields set to False."""
    state = OnboardingState()
    assert state.completed is False
    assert state.api_key_configured is False
    assert state.model_selected is False
    assert state.workspace_trusted is False


def test_check_onboarding_state_detects_api_key_from_env():
    """Test that check_onboarding_state detects API key from environment variables."""
    with patch.dict(os.environ, {"KODER_API_KEY": "test-key"}, clear=False):
        state = check_onboarding_state()
        assert state.api_key_configured is True


def test_check_onboarding_state_accepts_session_env_mapping(tmp_path):
    """Test that session-scoped env can satisfy onboarding checks."""
    (tmp_path / ".koder").mkdir()

    state = check_onboarding_state(
        project_dir=tmp_path,
        env={"KODER_API_KEY": "session-key", "KODER_MODEL": "gpt-4.1"},
    )

    assert state.api_key_configured is True
    assert state.model_selected is True
    assert state.workspace_trusted is True
    assert state.completed is True


def test_check_onboarding_state_detects_openai_api_key():
    """Test that check_onboarding_state detects OPENAI_API_KEY."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False):
        state = check_onboarding_state()
        assert state.api_key_configured is True


def test_check_onboarding_state_detects_anthropic_api_key():
    """Test that check_onboarding_state detects ANTHROPIC_API_KEY."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=False):
        state = check_onboarding_state()
        assert state.api_key_configured is True


def test_check_onboarding_state_no_api_key():
    """Test that check_onboarding_state returns False when no API key is set."""
    # Clear all possible API key env vars by removing them
    env_vars_to_clear = [
        "KODER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GITHUB_TOKEN",
    ]
    # Save current values
    original_values = {k: os.environ.get(k) for k in env_vars_to_clear}
    # Remove all API keys
    for key in env_vars_to_clear:
        os.environ.pop(key, None)

    try:
        state = check_onboarding_state()
        assert state.api_key_configured is False
    finally:
        # Restore original values
        for key, value in original_values.items():
            if value is not None:
                os.environ[key] = value


def test_check_onboarding_state_detects_model_from_env():
    """Test that check_onboarding_state detects model from KODER_MODEL env var."""
    with patch.dict(os.environ, {"KODER_MODEL": "gpt-4o"}, clear=False):
        state = check_onboarding_state()
        assert state.model_selected is True


def test_check_onboarding_state_no_model():
    """Test that model_selected defaults to True because koder has a built-in default model."""
    with patch.dict(os.environ, {"KODER_MODEL": ""}, clear=False):
        state = check_onboarding_state()
        # koder always has a built-in default model, so model_selected is True
        assert state.model_selected is True


def test_check_onboarding_state_detects_workspace(tmp_path):
    """Test that check_onboarding_state detects existing .koder/ directory."""
    koder_dir = tmp_path / ".koder"
    koder_dir.mkdir()

    state = check_onboarding_state(project_dir=tmp_path)
    assert state.workspace_trusted is True


def test_check_onboarding_state_no_workspace(tmp_path):
    """Test that check_onboarding_state returns False for new workspace."""
    state = check_onboarding_state(project_dir=tmp_path)
    assert state.workspace_trusted is False


def test_check_onboarding_state_completed_when_all_true(tmp_path):
    """Test that completed is True when all other fields are True."""
    koder_dir = tmp_path / ".koder"
    koder_dir.mkdir()

    with patch.dict(
        os.environ, {"KODER_API_KEY": "test-key", "KODER_MODEL": "gpt-4o"}, clear=False
    ):
        state = check_onboarding_state(project_dir=tmp_path)
        assert state.api_key_configured is True
        assert state.model_selected is True
        assert state.workspace_trusted is True
        assert state.completed is True


def test_get_onboarding_steps_returns_steps_for_unconfigured_state():
    """Test that get_onboarding_steps returns all steps for unconfigured state."""
    state = OnboardingState(
        completed=False,
        api_key_configured=False,
        model_selected=False,
        workspace_trusted=False,
    )
    steps = get_onboarding_steps(state)

    assert len(steps) > 0
    assert any("API key" in step or "api key" in step.lower() for step in steps)
    assert any("model" in step.lower() for step in steps)
    assert any("workspace" in step.lower() or "trust" in step.lower() for step in steps)


def test_get_onboarding_steps_partial_configuration():
    """Test that get_onboarding_steps returns only missing steps."""
    state = OnboardingState(
        completed=False,
        api_key_configured=True,
        model_selected=False,
        workspace_trusted=False,
    )
    steps = get_onboarding_steps(state)

    assert len(steps) > 0
    assert not any("API key" in step or "api key" in step.lower() for step in steps)
    assert any("model" in step.lower() for step in steps)


def test_get_onboarding_steps_returns_empty_for_fully_configured_state():
    """Test that get_onboarding_steps returns empty list for fully configured state."""
    state = OnboardingState(
        completed=True,
        api_key_configured=True,
        model_selected=True,
        workspace_trusted=True,
    )
    steps = get_onboarding_steps(state)

    assert len(steps) == 0


def test_mark_onboarding_complete_creates_marker_file(tmp_path):
    """Test that mark_onboarding_complete creates .koder/onboarded marker file."""
    mark_onboarding_complete(tmp_path)

    marker_file = tmp_path / ".koder" / "onboarded"
    assert marker_file.exists()
    assert marker_file.is_file()


def test_mark_onboarding_complete_creates_koder_dir_if_missing(tmp_path):
    """Test that mark_onboarding_complete creates .koder/ directory if it doesn't exist."""
    koder_dir = tmp_path / ".koder"
    assert not koder_dir.exists()

    mark_onboarding_complete(tmp_path)

    assert koder_dir.exists()
    assert koder_dir.is_dir()
    assert (koder_dir / "onboarded").exists()


def test_is_onboarding_complete_checks_marker(tmp_path):
    """Test that is_onboarding_complete returns True when marker file exists."""
    marker_file = tmp_path / ".koder" / "onboarded"
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    marker_file.touch()

    assert is_onboarding_complete(tmp_path) is True


def test_is_onboarding_complete_returns_false_without_marker(tmp_path):
    """Test that is_onboarding_complete returns False when marker file doesn't exist."""
    assert is_onboarding_complete(tmp_path) is False


def test_is_onboarding_complete_returns_false_when_koder_dir_missing(tmp_path):
    """Test that is_onboarding_complete returns False when .koder/ doesn't exist."""
    assert not (tmp_path / ".koder").exists()
    assert is_onboarding_complete(tmp_path) is False
