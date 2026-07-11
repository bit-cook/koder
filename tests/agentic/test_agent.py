import asyncio
import copy

import litellm.exceptions
from agents.extensions.models.litellm_model import LitellmModel

from koder_agent.agentic.agent import RetryingLitellmModel, create_dev_agent

_MISSING = object()


def _retryable_error(message="boom"):
    return litellm.exceptions.InternalServerError(
        message=message, model="test-model", llm_provider="test-provider"
    )


class DummyTool:
    def __init__(self, name="tool", schema=_MISSING, strict_json_schema=_MISSING):
        self.name = name
        if schema is not _MISSING:
            self.params_json_schema = schema
        if strict_json_schema is not _MISSING:
            self.strict_json_schema = strict_json_schema


def _make_model(model_name: str) -> RetryingLitellmModel:
    model = RetryingLitellmModel.__new__(RetryingLitellmModel)
    model.model = model_name
    return model


def _schema_with_refs() -> dict:
    return {
        "type": "object",
        "$defs": {
            "Foo": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            }
        },
        "properties": {
            "foo": {"$ref": "#/$defs/Foo"},
        },
    }


def test_is_github_copilot_true():
    model = _make_model("github_copilot/anthropic/claude-3")
    assert model._is_github_copilot()

    model = _make_model("Litellm/GitHub_Copilot/claude-3")
    assert model._is_github_copilot()


def test_is_github_copilot_false():
    for name in ["gpt-4", "claude-3"]:
        model = _make_model(name)
        assert not model._is_github_copilot()


def test_clean_tools_no_copilot_returns_unchanged():
    model = _make_model("gpt-4")
    schema = _schema_with_refs()
    tool = DummyTool(name="tool", schema=schema, strict_json_schema=True)
    tools = [tool]

    result = model._clean_tools_for_github_copilot(tools)

    assert result is tools
    assert tool.params_json_schema is schema
    assert "$ref" in tool.params_json_schema["properties"]["foo"]
    assert tool.strict_json_schema is True


def test_clean_tools_for_copilot_cleans_schema_and_strict():
    model = _make_model("github_copilot/anthropic/claude-3")
    tool_with_schema = DummyTool(
        name="tool",
        schema=copy.deepcopy(_schema_with_refs()),
        strict_json_schema=True,
    )
    tool_without_schema = DummyTool(
        name="no_schema",
        schema=_MISSING,
        strict_json_schema=True,
    )
    tools = [tool_with_schema, tool_without_schema]

    result = model._clean_tools_for_github_copilot(tools)

    assert result is tools
    cleaned_schema = tool_with_schema.params_json_schema
    assert "$defs" not in cleaned_schema
    assert "$ref" not in cleaned_schema.get("properties", {}).get("foo", {})
    assert tool_with_schema.strict_json_schema is False

    assert not hasattr(tool_without_schema, "params_json_schema")
    assert tool_without_schema.strict_json_schema is True


def test_clean_tools_handles_empty_list():
    model = _make_model("github_copilot/anthropic/claude-3")
    tools = []

    result = model._clean_tools_for_github_copilot(tools)

    assert result is tools
    assert result == []


def test_stream_response_retries_before_first_chunk(monkeypatch):
    """A retryable error raised before any chunk is yielded triggers a retry
    and ultimately succeeds."""
    model = _make_model("gpt-4")  # not the responses API path
    calls = {"n": 0}

    async def fake_super_stream(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _retryable_error("transient")
        for chunk in ["a", "b", "c"]:
            yield chunk

    monkeypatch.setattr(LitellmModel, "stream_response", fake_super_stream)
    # Avoid real sleeps between retries.
    monkeypatch.setattr("koder_agent.agentic.agent.asyncio.sleep", _no_sleep)

    async def run():
        out = []
        async for chunk in model.stream_response(
            None, "in", _DummySettings(), [], None, [], _DummyTracing()
        ):
            out.append(chunk)
        return out

    result = asyncio.run(run())

    assert result == ["a", "b", "c"]
    assert calls["n"] == 2  # failed once, retried, succeeded


def test_stream_response_does_not_retry_after_first_chunk(monkeypatch):
    """A retryable error raised AFTER a chunk has been yielded propagates and is
    NOT retried (retrying would replay partial output)."""
    model = _make_model("gpt-4")
    calls = {"n": 0}

    async def fake_super_stream(*args, **kwargs):
        calls["n"] += 1
        yield "first"
        raise _retryable_error("mid-stream")

    monkeypatch.setattr(LitellmModel, "stream_response", fake_super_stream)
    monkeypatch.setattr("koder_agent.agentic.agent.asyncio.sleep", _no_sleep)

    async def run():
        out = []
        async for chunk in model.stream_response(
            None, "in", _DummySettings(), [], None, [], _DummyTracing()
        ):
            out.append(chunk)
        return out

    raised = None
    try:
        asyncio.run(run())
    except litellm.exceptions.InternalServerError as exc:
        raised = exc

    assert raised is not None  # error propagated
    assert calls["n"] == 1  # only one attempt, no retry


def test_get_response_retries(monkeypatch):
    """get_response retry still works (fails once then succeeds)."""
    model = _make_model("gpt-4")
    calls = {"n": 0}
    sentinel = object()

    async def fake_super_get(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _retryable_error("transient-get")
        return sentinel

    monkeypatch.setattr(LitellmModel, "get_response", fake_super_get)
    monkeypatch.setattr("koder_agent.agentic.agent.asyncio.sleep", _no_sleep)
    # backoff uses time.sleep for the sync wrapper around the coroutine driver;
    # patch the module the decorator uses so retries don't actually wait.
    monkeypatch.setattr("backoff._async.asyncio.sleep", _no_sleep, raising=False)

    async def run():
        return await model.get_response(None, "in", _DummySettings(), [], None, [], _DummyTracing())

    result = asyncio.run(run())

    assert result is sentinel
    assert calls["n"] == 2  # failed once, retried, succeeded


class _DummySettings:
    def to_json_dict(self):
        return {}


class _DummyTracing:
    def is_disabled(self):
        return True

    def include_data(self):
        return False


async def _no_sleep(_seconds):
    return None


def test_create_dev_agent_uses_model_client_snapshot_for_litellm(monkeypatch):
    seen = {}

    def fake_snapshot(model_override):
        seen["model_override"] = model_override
        return {
            "model_name": "litellm/claude/claude-sonnet-4-6",
            "api_key": "oauth-access-token",
            "base_url": None,
            "native_openai": False,
            "litellm_kwargs": {
                "model": "claude/claude-sonnet-4-6",
                "api_key": "oauth-access-token",
                "base_url": None,
                "extra_headers": {"x-oauth-provider": "claude"},
            },
        }

    monkeypatch.setenv("KODER_SIMPLE", "1")
    monkeypatch.setattr("koder_agent.agentic.agent.get_model_client_snapshot", fake_snapshot)

    agent = asyncio.run(create_dev_agent([], model_override="inherit"))

    assert seen["model_override"] is None
    assert isinstance(agent.model, RetryingLitellmModel)
    assert agent.model.model == "claude/claude-sonnet-4-6"
    assert agent.model.api_key == "oauth-access-token"
    assert agent.model_settings.extra_headers == {"x-oauth-provider": "claude"}


def test_create_dev_agent_requests_reasoning_summary_when_display_enabled(monkeypatch):
    from koder_agent.harness.config.schema import RuntimeConfig

    config = RuntimeConfig()
    config.model.name = "gpt-5"
    config.model.provider = "openai"
    config.harness.reasoning_display = "summary"

    def fake_snapshot(_model_override):
        return {
            "model_name": "gpt-5",
            "api_key": "sk-test",
            "base_url": None,
            "native_openai": True,
            "litellm_kwargs": {},
        }

    monkeypatch.setenv("KODER_SIMPLE", "1")
    monkeypatch.delenv("KODER_REASONING_DISPLAY", raising=False)
    monkeypatch.setattr("koder_agent.agentic.agent.get_config", lambda: config)
    monkeypatch.setattr("koder_agent.agentic.agent.get_model_client_snapshot", fake_snapshot)
    monkeypatch.setattr("koder_agent.agentic.agent.should_use_reasoning_param", lambda: True)

    agent = asyncio.run(create_dev_agent([]))

    assert agent.model == "gpt-5"
    assert agent.model_settings.reasoning.summary == "detailed"
    assert agent.model_settings.reasoning.effort == "medium"


def test_create_dev_agent_passes_max_reasoning_effort_without_conversion(monkeypatch):
    from koder_agent.harness.config.schema import RuntimeConfig

    config = RuntimeConfig(
        model={
            "name": "gpt-5.6",
            "provider": "openai",
            "reasoning_effort": "max",
        }
    )

    def fake_snapshot(_model_override):
        return {
            "model_name": "gpt-5.6",
            "api_key": "sk-test",
            "base_url": None,
            "native_openai": True,
            "litellm_kwargs": {},
        }

    monkeypatch.setenv("KODER_SIMPLE", "1")
    monkeypatch.setattr("koder_agent.agentic.agent.get_config", lambda: config)
    monkeypatch.setattr("koder_agent.agentic.agent.get_model_client_snapshot", fake_snapshot)
    monkeypatch.setattr("koder_agent.agentic.agent.should_use_reasoning_param", lambda: True)

    agent = asyncio.run(create_dev_agent([]))

    assert agent.model_settings.reasoning.effort == "max"
    assert agent.model_settings.to_json_dict()["reasoning"]["effort"] == "max"
