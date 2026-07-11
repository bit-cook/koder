"""Tests for onboarding wizard state management."""

import logging
import os
from unittest.mock import patch

import pytest
import yaml

from koder_agent.config import reset_config_manager
from koder_agent.config.manager import ConfigManager
from koder_agent.harness.onboarding import (
    OnboardingState,
    check_onboarding_state,
    get_onboarding_steps,
    is_onboarding_complete,
    mark_onboarding_complete,
)

LOGGER_NAME = "koder_agent.harness.onboarding"


def _write_config(tmp_path, data: dict) -> None:
    config_path = tmp_path / ".koder" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    reset_config_manager()


@pytest.fixture(autouse=True)
def isolate_onboarding_config(monkeypatch, tmp_path):
    config_path = tmp_path / ".koder" / "config.yaml"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", config_path)
    for name in (
        "KODER_MODEL",
        "KODER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "AZURE_API_KEY",
        "OPENROUTER_API_KEY",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_config_manager()
    yield
    reset_config_manager()


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
    with patch.dict(
        os.environ,
        {
            "KODER_MODEL": "anthropic/claude-sonnet-4-6",
            "ANTHROPIC_API_KEY": "sk-ant-test",
        },
        clear=False,
    ):
        state = check_onboarding_state()
        assert state.api_key_configured is True


def test_check_onboarding_state_accepts_config_only_api_key(tmp_path):
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "gpt-4o",
                "provider": "openai",
                "api_key": "synthetic-config-key",
            }
        },
    )

    state = check_onboarding_state(env={})

    assert state.api_key_configured is True


@pytest.mark.parametrize(
    ("model", "env_name"),
    [
        ("azure/gpt-4o-mini", "AZURE_API_KEY"),
        ("gemini/gemini-2.5-pro", "GEMINI_API_KEY"),
        ("openrouter/anthropic/claude-3-opus", "OPENROUTER_API_KEY"),
    ],
)
def test_check_onboarding_state_uses_selected_provider_environment(model, env_name):
    state = check_onboarding_state(
        env={
            "KODER_MODEL": model,
            env_name: "synthetic-selected-provider-key",
        }
    )

    assert state.api_key_configured is True


def test_check_onboarding_state_ignores_unrelated_provider_key():
    state = check_onboarding_state(
        env={
            "KODER_MODEL": "openrouter/anthropic/claude-3-opus",
            "OPENAI_API_KEY": "synthetic-unrelated-key",
        }
    )

    assert state.api_key_configured is False


@pytest.mark.parametrize("provider", ["ollama", "vertex_ai", "bedrock"])
def test_provider_managed_auth_does_not_request_api_key(provider):
    state = check_onboarding_state(env={"KODER_MODEL": f"{provider}/test-model"})

    assert state.api_key_configured is True
    assert not any("api key" in step.lower() for step in get_onboarding_steps(state))


def test_check_onboarding_state_detects_litellm_copilot_token(tmp_path):
    """Test that LiteLLM's Copilot device-flow token satisfies auth setup."""
    token_dir = tmp_path / "copilot-token"
    token_dir.mkdir()
    (token_dir / "access-token").write_text("token", encoding="utf-8")

    state = check_onboarding_state(
        env={
            "KODER_MODEL": "github_copilot/claude-sonnet-4",
            "GITHUB_COPILOT_TOKEN_DIR": str(token_dir),
        }
    )

    assert state.api_key_configured is True
    assert state.auth_provider_hint == "github_copilot"


def test_check_onboarding_state_detects_custom_litellm_copilot_token_file(tmp_path):
    token_dir = tmp_path / "copilot-token"
    token_dir.mkdir()
    (token_dir / "custom-access-token").write_text("token", encoding="utf-8")

    state = check_onboarding_state(
        env={
            "KODER_MODEL": "github_copilot/claude-sonnet-4",
            "GITHUB_COPILOT_TOKEN_DIR": str(token_dir),
            "GITHUB_COPILOT_ACCESS_TOKEN_FILE": "custom-access-token",
        }
    )

    assert state.api_key_configured is True


def test_copilot_selected_from_session_mapping_ignores_ordinary_api_keys(tmp_path):
    token_dir = tmp_path / "missing-copilot-token"

    state = check_onboarding_state(
        env={
            "KODER_MODEL": "github_copilot/claude-sonnet-4",
            "KODER_API_KEY": "synthetic-koder-key",
            "GITHUB_TOKEN": "synthetic-github-token",
            "OPENAI_API_KEY": "synthetic-openai-key",
            "GITHUB_COPILOT_TOKEN_DIR": str(token_dir),
        }
    )

    assert state.api_key_configured is False
    assert state.auth_provider_hint == "github_copilot"


def test_copilot_path_does_not_call_shared_auth_resolver(monkeypatch, tmp_path):
    token_dir = tmp_path / "missing-copilot-token"
    (tmp_path / ".koder").mkdir()
    resolver = patch(
        "koder_agent.harness.onboarding.resolve_auth_status",
        side_effect=RuntimeError("resolver-must-not-run"),
    )

    with resolver as mock_resolver:
        state = check_onboarding_state(
            project_dir=tmp_path,
            env={
                "KODER_MODEL": "github_copilot/claude-sonnet-4",
                "GITHUB_COPILOT_TOKEN_DIR": str(token_dir),
            },
        )

    mock_resolver.assert_not_called()
    assert state.api_key_configured is False
    assert state.auth_provider_hint == "github_copilot"
    assert get_onboarding_steps(state) == [
        "Authenticate GitHub Copilot: run koder auth login github_copilot"
    ]


def test_selected_environment_model_overrides_copilot_config(tmp_path):
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "claude-sonnet-4",
                "provider": "github_copilot",
            }
        },
    )

    state = check_onboarding_state(
        env={
            "KODER_MODEL": "openrouter/anthropic/claude-3-opus",
            "OPENROUTER_API_KEY": "synthetic-openrouter-key",
        }
    )

    assert state.api_key_configured is True
    assert state.auth_provider_hint is None


def test_check_onboarding_state_no_api_key():
    """Test that check_onboarding_state returns False when no API key is set."""
    # Clear all possible API key env vars by removing them
    env_vars_to_clear = [
        "KODER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
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


def test_get_onboarding_steps_uses_copilot_auth_message():
    state = OnboardingState(
        completed=False,
        api_key_configured=False,
        model_selected=True,
        workspace_trusted=True,
        auth_provider_hint="github_copilot",
    )

    steps = get_onboarding_steps(state)

    assert steps == ["Authenticate GitHub Copilot: run koder auth login github_copilot"]


@pytest.mark.parametrize(
    ("provider", "label"),
    [
        ("claude", "Claude"),
        ("chatgpt", "ChatGPT"),
        ("google", "Google"),
        ("antigravity", "Antigravity"),
    ],
)
def test_oauth_provider_failure_uses_provider_specific_login_guidance(
    monkeypatch, tmp_path, provider, label
):
    (tmp_path / ".koder").mkdir()
    monkeypatch.setattr(
        "koder_agent.auth.client_integration.get_oauth_api_key",
        lambda _provider: None,
    )

    state = check_onboarding_state(
        project_dir=tmp_path,
        env={"KODER_MODEL": f"{provider}/test-model"},
    )

    assert state.api_key_configured is False
    assert state.auth_provider_hint == provider
    assert get_onboarding_steps(state) == [f"Authenticate {label}: run koder auth login {provider}"]


def test_oauth_failure_with_api_key_fallback_needs_no_login(monkeypatch, tmp_path):
    (tmp_path / ".koder").mkdir()
    monkeypatch.setattr(
        "koder_agent.auth.client_integration.get_oauth_api_key",
        lambda _provider: None,
    )

    state = check_onboarding_state(
        project_dir=tmp_path,
        env={
            "KODER_MODEL": "claude/claude-sonnet-4-6",
            "CLAUDE_API_KEY": "synthetic-fallback-key",
        },
    )

    assert state.completed is True
    assert get_onboarding_steps(state) == []


def test_auth_resolution_error_is_contained_and_sanitized(monkeypatch, tmp_path, caplog):
    secret = "synthetic-resolver-error-secret"

    def fail_resolution(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        "koder_agent.harness.onboarding.resolve_auth_status",
        fail_resolution,
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        state = check_onboarding_state(
            project_dir=tmp_path,
            env={"KODER_MODEL": "openrouter/test-model"},
        )

    assert state.api_key_configured is False
    assert get_onboarding_steps(state)[0].startswith("Configure API key:")
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text


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


def test_marker_does_not_bypass_live_credential_check(tmp_path):
    mark_onboarding_complete(tmp_path)

    state = check_onboarding_state(project_dir=tmp_path, env={})

    assert is_onboarding_complete(tmp_path) is True
    assert state.workspace_trusted is True
    assert state.api_key_configured is False
    assert state.completed is False
