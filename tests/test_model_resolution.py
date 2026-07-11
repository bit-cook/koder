import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Literal, get_type_hints

import pytest
import yaml

from koder_agent.config import reset_config_manager
from koder_agent.config.manager import ConfigManager
from koder_agent.utils import client as client_module
from koder_agent.utils.client import (
    get_api_key,
    get_base_url,
    get_litellm_model_kwargs,
    get_model_client_snapshot,
    get_model_name,
    is_native_openai_provider,
)


def _write_config(tmp_path, data: dict) -> None:
    """Write a config file under the temp HOME used for tests."""
    config_dir = tmp_path / ".koder"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_auth_resolution_status_is_frozen():
    assert hasattr(client_module, "AuthResolutionStatus")
    status = client_module.AuthResolutionStatus(provider="openai", configured=False)

    assert status.oauth_provider is None
    assert status.credential_mode == "api_key"
    with pytest.raises(FrozenInstanceError):
        status.configured = True


def test_auth_resolution_status_credential_mode_is_literal():
    assert (
        get_type_hints(client_module.AuthResolutionStatus)["credential_mode"]
        == Literal["api_key", "provider_managed"]
    )


def test_auth_resolution_status_rejects_unconfigured_provider_managed():
    with pytest.raises(ValueError, match="provider_managed.*configured"):
        client_module.AuthResolutionStatus(
            provider="ollama",
            configured=False,
            credential_mode="provider_managed",
        )


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    """
    Isolate HOME and clear relevant env vars between tests.

    The client code reads ~/.koder/config.yaml via Path.home(), so we
    point HOME to a temp directory and reset the config manager cache.
    """
    # Redirect config location to temp HOME
    config_path = Path(tmp_path) / ".koder" / "config.yaml"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", config_path)

    for var in [
        "KODER_MODEL",
        "KODER_API_KEY",
        "KODER_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "AZURE_API_KEY",
        "AZURE_API_BASE",
        "AZURE_API_VERSION",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GITHUB_TOKEN",
        "CHATGPT_API_KEY",
        "ANTIGRAVITY_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)
    reset_config_manager()
    yield
    reset_config_manager()


def test_env_model_provider_overrides_config(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4.1", "provider": "openai"}},
    )
    monkeypatch.setenv("KODER_MODEL", "openrouter/x-ai/grok-4.1-fast:free")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    # Env-supplied provider should be used and normalized for LiteLLM
    assert get_model_name() == "litellm/openrouter/x-ai/grok-4.1-fast:free"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["model"] == "openrouter/x-ai/grok-4.1-fast:free"


@pytest.mark.parametrize(
    ("env_model", "env_vars", "expected_model", "expected_litellm_model", "expected_native"),
    [
        (
            "openai/gpt-4o",
            {"OPENAI_API_KEY": "sk-openai"},
            "gpt-4o",
            "openai/gpt-4o",
            True,
        ),
        (
            "anthropic/claude-opus-4-1",
            {"ANTHROPIC_API_KEY": "sk-anthropic"},
            "litellm/anthropic/claude-opus-4-1",
            "anthropic/claude-opus-4-1",
            False,
        ),
        (
            "google/gemini-2.5-pro",
            {"GOOGLE_API_KEY": "sk-google"},
            "litellm/gemini/gemini-2.5-pro",
            "gemini/gemini-2.5-pro",
            False,
        ),
        (
            "claude/claude-opus-4-1",
            {"KODER_API_KEY": "sk-claude"},
            "litellm/anthropic/claude-opus-4-1",
            "anthropic/claude-opus-4-1",
            False,
        ),
        (
            "chatgpt/gpt-5.2",
            {"KODER_API_KEY": "sk-chatgpt"},
            "litellm/openai/gpt-5.2",
            "openai/gpt-5.2",
            False,
        ),
        (
            "antigravity/gemini-3-pro-high",
            {"KODER_API_KEY": "sk-antigravity"},
            "litellm/antigravity/gemini-3-pro-high",
            "antigravity/gemini-3-pro-high",
            False,
        ),
        (
            "github_copilot/gpt-5.1-codex",
            {},
            "litellm/github_copilot/gpt-5.1-codex",
            "github_copilot/gpt-5.1-codex",
            False,
        ),
        (
            "openai/local-model",
            {"KODER_API_KEY": "sk-custom", "KODER_BASE_URL": "http://localhost:8080/v1"},
            "litellm/openai/local-model",
            "openai/local-model",
            False,
        ),
    ],
)
def test_provider_family_model_routing_matrix(
    monkeypatch,
    tmp_path,
    env_model,
    env_vars,
    expected_model,
    expected_litellm_model,
    expected_native,
):
    _write_config(tmp_path, {"model": {"name": "gpt-4.1", "provider": "openai"}})
    monkeypatch.setenv("KODER_MODEL", env_model)
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    assert get_model_name() == expected_model
    assert is_native_openai_provider() is expected_native
    kwargs = get_litellm_model_kwargs()
    assert kwargs["model"] == expected_litellm_model
    if "KODER_BASE_URL" in env_vars:
        assert kwargs["base_url"] == env_vars["KODER_BASE_URL"]


def test_openai_native_model_uses_raw(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4.1", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert get_model_name() == "gpt-4.1"
    assert is_native_openai_provider()


def test_openai_provider_non_openai_model_uses_litellm(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "x-ai/grok-4.1-fast:free", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    model = get_model_name()
    assert model == "litellm/openai/x-ai/grok-4.1-fast:free"
    assert not is_native_openai_provider()
    kwargs = get_litellm_model_kwargs()
    assert kwargs["model"] == "openai/x-ai/grok-4.1-fast:free"


def test_azure_provider_uses_litellm_and_base_url(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o-mini", "provider": "azure"}},
    )
    monkeypatch.setenv("AZURE_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_API_BASE", "https://example.azure.com")
    monkeypatch.setenv("AZURE_API_VERSION", "2025-04-01-preview")

    # Azure should always go through LiteLLM path
    assert get_model_name() == "litellm/azure/gpt-4o-mini"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["model"] == "azure/gpt-4o-mini"
    assert kwargs["base_url"] == "https://example.azure.com"
    assert kwargs["api_key"] == "azure-key"


def test_openrouter_config_path(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "anthropic/claude-3-opus", "provider": "openrouter"}},
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-openrouter")

    assert get_model_name() == "litellm/openrouter/anthropic/claude-3-opus"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["model"] == "openrouter/anthropic/claude-3-opus"
    assert kwargs["api_key"] == "sk-openrouter"


def test_env_openai_model_overrides_non_openai_config(monkeypatch, tmp_path):
    # Config says azure, but KODER_MODEL supplies an OpenAI-native model with provider prefix
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o-mini", "provider": "azure"}},
    )
    monkeypatch.setenv("KODER_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert get_model_name() == "gpt-4o"
    assert is_native_openai_provider()


def test_model_override_resolves_provider_base_url_and_api_key(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4.1", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("KODER_BASE_URL", "http://proxy.local/v1")

    assert get_model_name("anthropic/claude-sonnet-4-6") == ("litellm/anthropic/claude-sonnet-4-6")
    assert not is_native_openai_provider("anthropic/claude-sonnet-4-6")
    kwargs = get_litellm_model_kwargs("anthropic/claude-sonnet-4-6")
    assert kwargs["model"] == "anthropic/claude-sonnet-4-6"
    assert kwargs["api_key"] == "sk-anthropic"
    assert kwargs["base_url"] == "http://proxy.local/v1"


def test_model_client_snapshot_honors_openai_native_override(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "claude-opus-4-6", "provider": "anthropic"}},
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("KODER_REASONING_EFFORT", "high")

    snapshot = get_model_client_snapshot("openai/gpt-4o")

    assert snapshot["model_name"] == "gpt-4o"
    assert snapshot["api_key"] == "sk-openai"
    assert snapshot["native_openai"] is True
    assert snapshot["reasoning_effort"] == "high"
    assert snapshot["litellm_kwargs"]["model"] == "openai/gpt-4o"


def test_oauth_main_model_snapshot_uses_oauth_provider_for_inherit(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "claude-sonnet-4-6", "provider": "claude"}},
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "invalid-anthropic-key")

    monkeypatch.setattr(
        "koder_agent.auth.client_integration.has_oauth_credentials",
        lambda provider: provider == "claude",
    )
    monkeypatch.setattr(
        "koder_agent.auth.client_integration.get_oauth_api_key",
        lambda provider: "oauth-access-token" if provider == "claude" else None,
    )
    monkeypatch.setattr(
        "koder_agent.auth.client_integration.has_oauth_token",
        lambda provider: provider == "claude",
    )
    monkeypatch.setattr(
        "koder_agent.auth.client_integration.get_oauth_headers",
        lambda provider: {"x-oauth-provider": provider} if provider == "claude" else {},
    )

    snapshot = get_model_client_snapshot()

    assert snapshot["model_name"] == "litellm/claude/claude-sonnet-4-6"
    assert snapshot["api_key"] == "oauth-access-token"
    assert snapshot["native_openai"] is False
    assert snapshot["litellm_kwargs"]["model"] == "claude/claude-sonnet-4-6"
    assert snapshot["litellm_kwargs"]["api_key"] == "oauth-access-token"
    assert snapshot["litellm_kwargs"]["extra_headers"] == {"x-oauth-provider": "claude"}


# =============================================================================
# KODER_BASE_URL Tests
# =============================================================================


def test_koder_base_url_overrides_config_base_url(monkeypatch, tmp_path):
    """KODER_BASE_URL env var should override base_url from config file."""
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "gpt-4o",
                "provider": "openai",
                "base_url": "http://config-file.local/v1",
            }
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("KODER_BASE_URL", "http://koder-override.local/v1")

    assert get_base_url() == "http://koder-override.local/v1"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["base_url"] == "http://koder-override.local/v1"


def test_koder_base_url_overrides_provider_env_var(monkeypatch, tmp_path):
    """KODER_BASE_URL should take priority over provider-specific env vars like OPENAI_BASE_URL."""
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai-specific.local/v1")
    monkeypatch.setenv("KODER_BASE_URL", "http://koder-override.local/v1")

    assert get_base_url() == "http://koder-override.local/v1"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["base_url"] == "http://koder-override.local/v1"


def test_provider_env_base_url_works_without_koder_base_url(monkeypatch, tmp_path):
    """Provider-specific env var (OPENAI_BASE_URL) should work when KODER_BASE_URL is not set."""
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai-specific.local/v1")

    assert get_base_url() == "http://openai-specific.local/v1"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["base_url"] == "http://openai-specific.local/v1"


def test_config_base_url_works_without_env_vars(monkeypatch, tmp_path):
    """Config file base_url should work when no env vars are set."""
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "gpt-4o",
                "provider": "openai",
                "base_url": "http://config-file.local/v1",
            }
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert get_base_url() == "http://config-file.local/v1"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["base_url"] == "http://config-file.local/v1"


def test_provider_env_base_url_overrides_config(monkeypatch, tmp_path):
    """Provider env var should override config file base_url (existing behavior)."""
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "gpt-4o",
                "provider": "openai",
                "base_url": "http://config-file.local/v1",
            }
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai-specific.local/v1")

    assert get_base_url() == "http://openai-specific.local/v1"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["base_url"] == "http://openai-specific.local/v1"


def test_azure_base_url_with_koder_override(monkeypatch, tmp_path):
    """KODER_BASE_URL should override Azure provider base_url."""
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o-mini", "provider": "azure"}},
    )
    monkeypatch.setenv("AZURE_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_API_BASE", "https://azure.example.com")
    monkeypatch.setenv("AZURE_API_VERSION", "2025-04-01-preview")
    monkeypatch.setenv("KODER_BASE_URL", "http://koder-override.local/v1")

    assert get_base_url() == "http://koder-override.local/v1"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["base_url"] == "http://koder-override.local/v1"


def test_no_base_url_returns_none(monkeypatch, tmp_path):
    """When no base_url is configured anywhere, get_base_url should return None."""
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert get_base_url() is None
    kwargs = get_litellm_model_kwargs()
    assert kwargs["base_url"] is None


# =============================================================================
# KODER_API_KEY Tests
# =============================================================================


def test_koder_api_key_overrides_config_api_key(monkeypatch, tmp_path):
    """KODER_API_KEY env var should override api_key from config file."""
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "gpt-4o",
                "provider": "openai",
                "api_key": "sk-from-config",
            }
        },
    )
    monkeypatch.setenv("KODER_API_KEY", "sk-koder-override")

    assert get_api_key() == "sk-koder-override"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["api_key"] == "sk-koder-override"


def test_injected_provider_env_does_not_mutate_process_environment(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o", "provider": "openai"}},
    )
    monkeypatch.setenv("KODER_API_KEY", "process-koder-key")
    monkeypatch.setenv("OPENAI_API_KEY", "process-provider-key")
    original_env = {
        "KODER_API_KEY": os.environ["KODER_API_KEY"],
        "OPENAI_API_KEY": os.environ["OPENAI_API_KEY"],
    }

    assert get_api_key(env={"OPENAI_API_KEY": "injected-provider-key"}) == ("injected-provider-key")
    assert {name: os.environ[name] for name in original_env} == original_env


def test_resolve_auth_status_uses_injected_openrouter_env_without_mutating_environ(
    monkeypatch, tmp_path
):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o", "provider": "openai"}},
    )
    monkeypatch.setenv("KODER_MODEL", "openai/process-model")
    monkeypatch.setenv("KODER_API_KEY", "process-koder-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "process-openrouter-key")
    before = dict(os.environ)

    status = client_module.resolve_auth_status(
        env={
            "KODER_MODEL": "openrouter/anthropic/claude-3-opus",
            "OPENROUTER_API_KEY": "synthetic-openrouter-key",
        }
    )

    assert status == client_module.AuthResolutionStatus(
        provider="openrouter",
        configured=True,
    )
    assert dict(os.environ) == before


def test_injected_model_ignores_unrelated_provider_key(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "gpt-4o",
                "provider": "openai",
                "api_key": "config-openai-key",
            }
        },
    )

    def fail_if_effective_value_reads_process_env(*args, **kwargs):
        raise AssertionError("injected model/key resolution must not read process env")

    monkeypatch.setattr(
        ConfigManager,
        "get_effective_value",
        fail_if_effective_value_reads_process_env,
    )

    assert (
        get_api_key(
            env={
                "KODER_MODEL": "anthropic/claude-sonnet-4-6",
                "OPENAI_API_KEY": "unrelated-openai-key",
            }
        )
        is None
    )


def test_injected_koder_api_key_wins_without_oauth(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "claude-sonnet-4-6", "provider": "claude"}},
    )

    def fail_if_oauth_runs(provider):
        raise AssertionError(f"OAuth should not run for {provider}")

    monkeypatch.setattr(
        "koder_agent.auth.client_integration.get_oauth_api_key",
        fail_if_oauth_runs,
    )

    assert (
        get_api_key(
            env={
                "KODER_API_KEY": "injected-koder-key",
                "CLAUDE_API_KEY": "injected-provider-key",
            }
        )
        == "injected-koder-key"
    )


def test_oauth_wins_over_injected_provider_env(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "claude-sonnet-4-6",
                "provider": "claude",
                "api_key": "config-provider-key",
            }
        },
    )
    oauth_calls = []

    def resolve_oauth(provider):
        oauth_calls.append(provider)
        return "oauth-access-token"

    monkeypatch.setattr(
        "koder_agent.auth.client_integration.get_oauth_api_key",
        resolve_oauth,
    )

    assert get_api_key(env={"CLAUDE_API_KEY": "injected-provider-key"}) == ("oauth-access-token")
    assert oauth_calls == ["claude"]


def test_oauth_failure_falls_back_to_injected_provider_env(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "claude-sonnet-4-6", "provider": "claude"}},
    )
    monkeypatch.setattr(
        "koder_agent.auth.client_integration.get_oauth_api_key",
        lambda provider: None,
    )

    assert get_api_key(env={"CLAUDE_API_KEY": "injected-provider-key"}) == ("injected-provider-key")


def test_oauth_failure_falls_back_to_matching_config(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "claude-sonnet-4-6",
                "provider": "claude",
                "api_key": "config-provider-key",
            }
        },
    )
    monkeypatch.setattr(
        "koder_agent.auth.client_integration.get_oauth_api_key",
        lambda provider: None,
    )

    assert get_api_key(env={"CLAUDE_API_KEY": None}) == "config-provider-key"


def test_injected_empty_provider_key_overrides_config(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "gpt-4o",
                "provider": "openai",
                "api_key": "config-provider-key",
            }
        },
    )

    assert get_api_key(env={"KODER_API_KEY": "", "OPENAI_API_KEY": ""}) == ""


def test_koder_api_key_overrides_provider_env_var(monkeypatch, tmp_path):
    """KODER_API_KEY should take priority over provider-specific env vars like OPENAI_API_KEY."""
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-specific")
    monkeypatch.setenv("KODER_API_KEY", "sk-koder-override")

    assert get_api_key() == "sk-koder-override"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["api_key"] == "sk-koder-override"


def test_koder_api_key_works_across_providers(monkeypatch, tmp_path):
    """KODER_API_KEY should work regardless of provider (OpenAI, Anthropic, etc.)."""
    # Test with Anthropic provider
    _write_config(
        tmp_path,
        {"model": {"name": "claude-3-opus", "provider": "anthropic"}},
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-specific")
    monkeypatch.setenv("KODER_API_KEY", "sk-koder-universal")

    assert get_api_key() == "sk-koder-universal"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["api_key"] == "sk-koder-universal"


def test_github_copilot_ignores_api_key_env_vars(monkeypatch, tmp_path):
    """GitHub Copilot uses LiteLLM device-flow auth, not KODER_API_KEY/GITHUB_TOKEN."""
    _write_config(
        tmp_path,
        {"model": {"name": "claude-sonnet-4", "provider": "github_copilot"}},
    )
    monkeypatch.setenv("KODER_API_KEY", "sk-koder-universal")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")

    assert get_api_key() is None
    kwargs = get_litellm_model_kwargs()
    assert kwargs["api_key"] is None


def test_resolve_auth_status_keeps_github_copilot_unconfigured(tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-5.1-codex", "provider": "github_copilot"}},
    )

    assert hasattr(client_module, "resolve_auth_status")
    status = client_module.resolve_auth_status(env={"KODER_API_KEY": "ignored-koder-key"})

    assert status == client_module.AuthResolutionStatus(
        provider="github_copilot",
        configured=False,
    )


def test_resolve_auth_status_contains_no_credential_value(tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o", "provider": "openai"}},
    )
    secret = "status-secret-canary"

    status = client_module.resolve_auth_status(env={"OPENAI_API_KEY": secret})

    assert status == client_module.AuthResolutionStatus(
        provider="openai",
        configured=True,
    )
    assert secret not in repr(status)
    assert secret not in status.__dict__.values()


def test_resolve_auth_status_reports_failed_oauth_provider(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "claude-sonnet-4-6", "provider": "claude"}},
    )
    monkeypatch.setattr(
        "koder_agent.auth.client_integration.get_oauth_api_key",
        lambda provider: None,
    )

    status = client_module.resolve_auth_status(env={})

    assert status == client_module.AuthResolutionStatus(
        provider="claude",
        configured=False,
        oauth_provider="claude",
    )


@pytest.mark.parametrize("provider", ["ollama", "vertex_ai", "bedrock"])
def test_resolve_auth_status_marks_provider_managed_auth_configured(tmp_path, provider):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o", "provider": "openai"}},
    )

    status = client_module.resolve_auth_status(env={"KODER_MODEL": f"{provider}/test-model"})

    assert status == client_module.AuthResolutionStatus(
        provider=provider,
        configured=True,
        credential_mode="provider_managed",
    )


def test_provider_env_api_key_works_without_koder_api_key(monkeypatch, tmp_path):
    """Provider-specific env var (OPENAI_API_KEY) should work when KODER_API_KEY is not set."""
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-specific")

    assert get_api_key() == "sk-openai-specific"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["api_key"] == "sk-openai-specific"


def test_config_api_key_works_without_env_vars(monkeypatch, tmp_path):
    """Config file api_key should work when no env vars are set."""
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "gpt-4o",
                "provider": "openai",
                "api_key": "sk-from-config",
            }
        },
    )

    assert get_api_key() == "sk-from-config"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["api_key"] == "sk-from-config"


def test_provider_env_api_key_overrides_config(monkeypatch, tmp_path):
    """Provider env var should override config file api_key (existing behavior)."""
    _write_config(
        tmp_path,
        {
            "model": {
                "name": "gpt-4o",
                "provider": "openai",
                "api_key": "sk-from-config",
            }
        },
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-specific")

    assert get_api_key() == "sk-openai-specific"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["api_key"] == "sk-openai-specific"


def test_azure_api_key_with_koder_override(monkeypatch, tmp_path):
    """KODER_API_KEY should override Azure provider api_key."""
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4o-mini", "provider": "azure"}},
    )
    monkeypatch.setenv("AZURE_API_KEY", "azure-key")
    monkeypatch.setenv("AZURE_API_BASE", "https://azure.example.com")
    monkeypatch.setenv("AZURE_API_VERSION", "2025-04-01-preview")
    monkeypatch.setenv("KODER_API_KEY", "sk-koder-override")

    assert get_api_key() == "sk-koder-override"
    kwargs = get_litellm_model_kwargs()
    assert kwargs["api_key"] == "sk-koder-override"


# =============================================================================
# OAuth vs API Provider Separation Tests
# =============================================================================


def test_api_providers_do_not_map_to_oauth():
    """API-based providers should NOT map to OAuth providers."""
    from koder_agent.auth.client_integration import map_provider_to_oauth

    assert map_provider_to_oauth("anthropic") is None
    assert map_provider_to_oauth("openai") is None
    assert map_provider_to_oauth("gemini") is None
    assert map_provider_to_oauth("azure") is None


def test_oauth_providers_map_to_themselves():
    """OAuth providers should map to themselves."""
    from koder_agent.auth.client_integration import map_provider_to_oauth

    assert map_provider_to_oauth("claude") == "claude"
    assert map_provider_to_oauth("chatgpt") == "chatgpt"
    assert map_provider_to_oauth("google") == "google"
    assert map_provider_to_oauth("antigravity") == "antigravity"
