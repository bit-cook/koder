import sys
import types

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

from koder_agent.harness.agents.definitions import AgentDefinition
from koder_agent.tools.task import TaskModel, _task_delegate_impl


def test_task_delegate_can_target_named_agent(monkeypatch):
    monkeypatch.delenv("KODER_SUBAGENT_MODEL", raising=False)
    seen_model_overrides = []

    async def fake_create_dev_agent(*args, **kwargs):
        seen_model_overrides.append(kwargs.get("model_override"))
        return object()

    class _Result:
        final_output = "delegated result"

    async def fake_run(*args, **kwargs):
        return _Result()

    monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
    monkeypatch.setattr("agents.Runner.run", fake_run)
    monkeypatch.setattr(
        "koder_agent.harness.agents.definitions.get_agent_definitions",
        lambda cwd: type(
            "Defs",
            (),
            {
                "active_agents": [
                    AgentDefinition(
                        agent_type="reviewer",
                        when_to_use="Reviews code",
                        system_prompt="You are a reviewer.",
                        source="built-in",
                    )
                ]
            },
        )(),
    )

    import asyncio

    output = asyncio.run(
        _task_delegate_impl(
            TaskModel(
                description="Review auth changes",
                prompt="Review auth changes",
                agent_type="reviewer",
            )
        )
    )

    assert "delegated result" in output
    assert seen_model_overrides == [None]


def test_task_delegate_respects_explicit_agent_model(monkeypatch):
    monkeypatch.delenv("KODER_SUBAGENT_MODEL", raising=False)
    seen_model_overrides = []

    async def fake_create_dev_agent(*args, **kwargs):
        seen_model_overrides.append(kwargs.get("model_override"))
        return object()

    class _Result:
        final_output = "delegated result"

    async def fake_run(*args, **kwargs):
        return _Result()

    monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
    monkeypatch.setattr("agents.Runner.run", fake_run)
    monkeypatch.setattr(
        "koder_agent.harness.agents.definitions.get_agent_definitions",
        lambda cwd: type(
            "Defs",
            (),
            {
                "active_agents": [
                    AgentDefinition(
                        agent_type="reviewer",
                        when_to_use="Reviews code",
                        system_prompt="You are a reviewer.",
                        source="built-in",
                        model="anthropic/claude-sonnet-4-6",
                    )
                ]
            },
        )(),
    )

    import asyncio

    output = asyncio.run(
        _task_delegate_impl(
            TaskModel(
                description="Review auth changes",
                prompt="Review auth changes",
                agent_type="reviewer",
            )
        )
    )

    assert "delegated result" in output
    assert seen_model_overrides == ["anthropic/claude-sonnet-4-6"]
