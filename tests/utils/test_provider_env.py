"""Security tests for provider environment setup (P1).

Verifies that ``_setup_provider_env_vars`` never plants a raw provider API key
into ``os.environ``. Subprocesses spawned by ``run_shell`` inherit the parent
environment, so a leaked key would be readable by the model via ``env`` /
``printenv``. Keys must flow only through explicit call kwargs.
"""

import os
from types import SimpleNamespace

import pytest

from koder_agent.utils.client import (
    _setup_provider_env_vars,
    get_provider_api_env_var,
)


def _make_config(provider, api_key, **extra):
    """Build a minimal fake config matching the ``config.model.*`` shape."""
    model = SimpleNamespace(
        provider=provider,
        api_key=api_key,
        base_url=extra.get("base_url"),
        azure_api_version=extra.get("azure_api_version"),
        vertex_ai_location=extra.get("vertex_ai_location"),
        vertex_ai_credentials_path=extra.get("vertex_ai_credentials_path"),
    )
    return SimpleNamespace(model=model)


@pytest.fixture
def clean_environ(monkeypatch):
    """Provide a hermetic, fully restored os.environ for the test body."""
    saved = dict(os.environ)
    # Strip provider key vars so the assertion can't pass spuriously.
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AZURE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.mark.parametrize(
    "provider",
    ["openai", "anthropic", "azure"],
)
def test_api_key_not_written_to_environ(clean_environ, provider):
    """The secret API key must NOT be placed into os.environ."""
    secret = "sk-super-secret-leak-canary-12345"
    config = _make_config(provider, api_key=secret, base_url="https://example.test")

    _setup_provider_env_vars(config, provider)

    # The provider's expected key env var must remain unset...
    env_var = get_provider_api_env_var(provider)
    assert os.environ.get(env_var) is None

    # ...and the secret must not appear in ANY environment variable.
    assert secret not in os.environ.values()


def test_non_secret_routing_vars_still_set(clean_environ):
    """Azure routing vars (non-secret) are still populated from config."""
    config = _make_config(
        "azure",
        api_key="sk-secret",
        base_url="https://my-azure.openai.azure.com",
        azure_api_version="2025-04-01-preview",
    )

    _setup_provider_env_vars(config, "azure")

    assert os.environ.get("AZURE_API_VERSION") == "2025-04-01-preview"
    assert os.environ.get("AZURE_API_BASE") == "https://my-azure.openai.azure.com"
    # But the secret key is still absent.
    assert os.environ.get("AZURE_API_KEY") is None
