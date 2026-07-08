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


def test_task_delegate_uses_deny_approver_not_inherited_prompt(monkeypatch):
    """Review finding 7: a delegated subagent must not inherit the interactive
    approver (which would let it prompt the user); it runs under an always-deny
    approver with the inherited permission service preserved."""
    import asyncio

    from koder_agent.tools.permission_context import (
        get_tool_permission_context,
        reset_tool_permission_context,
        set_tool_permission_context,
    )
    from koder_agent.tools.task import TaskModel, _task_delegate_impl

    captured = {}

    async def fake_create_dev_agent(*a, **k):
        return object()

    class _Result:
        final_output = "ok"

    async def fake_run(*a, **k):
        ctx = get_tool_permission_context()
        captured["approver"] = getattr(ctx, "approver", None)
        captured["service"] = getattr(ctx, "permission_service", None)
        return _Result()

    monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
    monkeypatch.setattr("agents.Runner.run", fake_run)

    inherited_service = object()

    async def scenario():
        # Parent scope has a service AND an interactive (prompt) approver.
        async def prompt_approver(*a, **k):
            return "allow"

        tok = set_tool_permission_context(inherited_service, approver=prompt_approver)
        try:
            await _task_delegate_impl(TaskModel(description="d", prompt="p"))
        finally:
            reset_tool_permission_context(tok)

    asyncio.run(scenario())
    # Service preserved, but the approver is swapped away from the prompt one.
    assert captured["service"] is inherited_service
    assert captured["approver"] is not None
    verdict = asyncio.run(captured["approver"]("run_shell", {"command": "rm -rf /"}, object()))
    assert verdict == "deny"
