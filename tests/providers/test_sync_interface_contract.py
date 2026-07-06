"""Contract tests: OAuth custom LLM providers are async-only.

Koder's runtime always calls providers through litellm's async entry points
(``acompletion`` / ``astreaming``). The sync ``completion()`` / ``streaming()``
methods required by the litellm CustomLLM interface are intentionally
unimplemented and must fail loudly with a message that names the async
alternative, so a future sync caller gets an actionable error instead of a
silent misbehavior.
"""

from __future__ import annotations

import pytest


def _provider_instances():
    from koder_agent.auth.providers.antigravity import AntigravityOAuthLLM
    from koder_agent.auth.providers.chatgpt import ChatGPTOAuthLLM
    from koder_agent.auth.providers.claude import ClaudeOAuthLLM
    from koder_agent.auth.providers.google import GoogleOAuthLLM

    return [
        ("claude", ClaudeOAuthLLM),
        ("chatgpt", ChatGPTOAuthLLM),
        ("google", GoogleOAuthLLM),
        ("antigravity", AntigravityOAuthLLM),
    ]


@pytest.mark.parametrize("name,provider_cls", _provider_instances())
def test_sync_completion_raises_not_implemented_naming_async_path(name, provider_cls):
    provider = provider_cls.__new__(provider_cls)  # skip auth-heavy __init__

    with pytest.raises(NotImplementedError, match="acompletion"):
        provider.completion("model", [])


@pytest.mark.parametrize("name,provider_cls", _provider_instances())
def test_sync_streaming_raises_not_implemented_naming_async_path(name, provider_cls):
    provider = provider_cls.__new__(provider_cls)

    with pytest.raises(NotImplementedError, match="astreaming"):
        provider.streaming("model", [])


@pytest.mark.parametrize("name,provider_cls", _provider_instances())
def test_async_entry_points_exist(name, provider_cls):
    import inspect

    assert inspect.iscoroutinefunction(provider_cls.acompletion)
    assert inspect.isasyncgenfunction(provider_cls.astreaming) or inspect.iscoroutinefunction(
        provider_cls.astreaming
    )
