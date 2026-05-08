"""Integration tests verifying the full agent tools workflow.

Tests cover:
- Tool registration in get_all_tools()
- Agent definition filtering with the Agent alias
- Sync agent spawn via _agent_tool_impl
- Full team lifecycle: create -> spawn teammate -> send message -> delete
"""

import asyncio
import json
import sys
import types
from pathlib import Path

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def test_agent_tool_is_registered_in_get_all_tools():
    """Agent and team tools appear in get_all_tools() by default."""
    from koder_agent.tools import get_all_tools

    tools = get_all_tools()
    names = {t.name for t in tools}
    assert "agent_tool" in names
    assert "send_message" in names
    assert "team_create" in names
    assert "team_delete" in names


def test_team_tools_included_without_env_var():
    """Team tools are always included without an env flag."""
    from koder_agent.tools import get_all_tools

    tools = get_all_tools()
    names = {t.name for t in tools}
    assert "send_message" in names
    assert "team_create" in names
    assert "team_delete" in names


def test_agent_tool_filtered_by_agent_definition():
    """Agent definition with tools=['Agent', 'Read'] includes agent_tool and read_file."""
    from koder_agent.harness.agents.definitions import (
        AgentDefinition,
        filter_tools_for_agent_definition,
    )
    from koder_agent.tools import get_all_tools

    all_tools = get_all_tools()
    defn = AgentDefinition(
        agent_type="test-agent",
        when_to_use="Testing",
        system_prompt="Test agent.",
        source="built-in",
        tools=["Agent", "Read"],
    )
    filtered = filter_tools_for_agent_definition(defn, all_tools)
    filtered_names = {t.name for t in filtered}
    assert "agent_tool" in filtered_names
    assert "read_file" in filtered_names
    assert "write_file" not in filtered_names


def test_full_sync_agent_spawn_and_result(monkeypatch):
    """Sync agent_tool call with subagent_type='Explore' returns sub-agent result."""

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        return "analysis complete: 5 endpoints found"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.tools.agent import _agent_tool_impl

    result = asyncio.run(
        _agent_tool_impl(
            description="API analysis",
            prompt="Find all REST endpoints",
            subagent_type="Explore",
        )
    )
    parsed = json.loads(result)
    assert parsed["status"] == "completed"
    assert "5 endpoints" in parsed["result"]
    assert parsed["agent_type"] == "Explore"


def test_full_team_workflow(tmp_path, monkeypatch):
    """End-to-end: create team, send live follow-up work, then shut the teammate down."""

    prompts: list[str] = []

    async def fake_execute(
        *, agent_definition, prompt, session_id, seed_items, cwd, team_context=None
    ):
        prompts.append(prompt)
        return "teammate done"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)
    monkeypatch.setattr(
        "koder_agent.harness.agents.teams.service.dispatch_project_hook_event",
        lambda **kw: types.SimpleNamespace(blocked=False),
    )

    from koder_agent.harness.agents.definitions import AgentDefinition
    from koder_agent.harness.agents.service import AgentService
    from koder_agent.harness.agents.teams.in_process import InProcessTeammateRunner
    from koder_agent.harness.agents.teams.service import TeamService
    from koder_agent.tools.send_message import _send_message_impl
    from koder_agent.tools.team import _team_create_impl, _team_delete_impl

    team_svc = TeamService.for_test(root=tmp_path)
    agent_svc = AgentService.for_test(tmp_path)

    async def run_case():
        # 1. Create team
        create_result = await _team_create_impl(team_name="dev-team", _team_service=team_svc)
        team_data = json.loads(create_result)
        assert team_data["status"] == "created"
        team_id = team_data["team_id"]

        # 2. Spawn teammate via InProcessTeammateRunner
        runner = InProcessTeammateRunner(agent_service=agent_svc, team_service=team_svc)
        definition = AgentDefinition(
            agent_type="general-purpose",
            when_to_use="General",
            system_prompt="Agent.",
            source="built-in",
        )
        spawn_result = await runner.spawn_teammate(
            team_id=team_id,
            name="coder",
            agent_definition=definition,
            prompt="Implement feature X",
            cwd=tmp_path,
        )
        await runner.wait(spawn_result.agent_id)

        # 3. Send message to teammate and let the live in-process worker pick it up
        msg_result = await _send_message_impl(
            to="coder",
            message="Please also add tests",
            summary="Add tests",
            _team_service=team_svc,
            _team_name=team_id,
        )
        msg_data = json.loads(msg_result)
        assert msg_data["status"] == "sent"
        deadline = asyncio.get_running_loop().time() + 2
        while len(prompts) < 2:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(f"Timed out waiting for follow-up prompt: {prompts}")
            await asyncio.sleep(0.05)
        await runner.wait(spawn_result.agent_id)
        assert prompts == ["Implement feature X", "Please also add tests"]

        # 4. Active idle teammates should block cleanup until shutdown
        blocked = await _team_delete_impl(_team_service=team_svc, _team_id=team_id)
        blocked_data = json.loads(blocked)
        assert blocked_data["status"] == "error"
        assert "active team" in blocked_data["error"]

        await runner.terminate(spawn_result.agent_id)
        del_result = await _team_delete_impl(_team_service=team_svc, _team_id=team_id)
        del_data = json.loads(del_result)
        assert del_data["status"] == "deleted"

    asyncio.run(run_case())
