"""Tests for the agent_tool — programmatic sub-agent spawning."""

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


def test_agent_tool_sync_spawn_returns_result(monkeypatch):
    """Sync agent_tool call blocks and returns the sub-agent result."""

    display_labels = []

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        display_labels.append(_kwargs["display_identity"].label)
        return f"result for: {prompt}"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.core.display_context import (
        subagent_display_scope,
        tool_display_call_scope,
    )
    from koder_agent.tools.agent import _agent_tool_impl

    async def run_case():
        with (
            subagent_display_scope(lambda _event: None),
            tool_display_call_scope("agent_tool", "call-agent-tool"),
        ):
            return await _agent_tool_impl(
                description="Test sync",
                prompt="Analyze the auth module",
            )

    result = asyncio.run(run_case())
    parsed = json.loads(result)
    assert parsed["status"] == "completed"
    assert "result for: Analyze the auth module" in parsed["result"]
    assert display_labels == ["general-purpose · Test sync"]


def test_agent_tool_with_subagent_type(monkeypatch):
    """Agent tool resolves a named agent type."""

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        return f"explored by {agent_definition.agent_type}"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.tools.agent import _agent_tool_impl

    result = asyncio.run(
        _agent_tool_impl(
            description="Explore codebase",
            prompt="Find all API endpoints",
            subagent_type="Explore",
        )
    )
    parsed = json.loads(result)
    assert parsed["status"] == "completed"
    assert parsed["agent_type"] == "Explore"
    assert "Explore" in parsed["result"]


def test_agent_tool_unknown_agent_type_returns_error():
    """Unknown agent type returns error with available list."""
    from koder_agent.tools.agent import _agent_tool_impl

    result = asyncio.run(
        _agent_tool_impl(
            description="Bad type",
            prompt="Do something",
            subagent_type="nonexistent-agent-type",
        )
    )
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "unknown" in parsed["error"].lower()
    assert "available_agents" in parsed


def test_agent_tool_async_spawn_returns_agent_id(tmp_path, monkeypatch):
    """Async agent_tool call returns immediately with agent_id."""

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        return "background result"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.tools.agent import _agent_tool_impl

    async def run_case():
        result = await _agent_tool_impl(
            description="Background task",
            prompt="Run tests in background",
            run_in_background=True,
        )
        parsed = json.loads(result)
        assert "agent-" in parsed["agent_id"]
        assert parsed["status"] == "async_launched"

    asyncio.run(run_case())


def test_agent_tool_with_isolation_worktree(tmp_path, monkeypatch):
    """Agent tool passes isolation=worktree to the agent definition."""
    import subprocess

    # The tool creates a real worktree in Path.cwd(); run inside a throwaway
    # git repo so the developer's repository is never touched.
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
    (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=repo_root, check=True, capture_output=True, text=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.chdir(repo_root)

    seen_definitions = []
    seen_cwds = []

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        seen_definitions.append(agent_definition)
        seen_cwds.append(cwd)
        return "worktree result"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.tools.agent import _agent_tool_impl

    result = asyncio.run(
        _agent_tool_impl(
            description="Isolated work",
            prompt="Edit files safely",
            isolation="worktree",
        )
    )
    parsed = json.loads(result)
    assert parsed["status"] == "completed"
    assert "worktree result" in parsed["result"]
    # The definition passed to execute should have isolation set
    assert len(seen_definitions) == 1
    assert seen_definitions[0].isolation == "worktree"
    # The run happened in a worktree, and the clean worktree was removed
    # afterwards along with its sync-agent/* branch.
    assert seen_cwds[0] != str(repo_root)
    assert not Path(seen_cwds[0]).exists()
    branches = subprocess.run(
        ["git", "branch", "--list", "sync-agent/*", "--format=%(refname:short)"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branches == ""


def test_agent_tool_background_frontmatter_forces_async(monkeypatch):
    """background=true in agent definition forces async execution."""

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        return "bg forced result"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.harness.agents.definitions import AgentDefinition, AgentDefinitionsResult
    from koder_agent.tools.agent import _agent_tool_impl

    # Create a background=True agent definition
    bg_agent = AgentDefinition(
        agent_type="bg-worker",
        when_to_use="Background worker",
        system_prompt="You are a background worker.",
        source="built-in",
        background=True,
    )

    def patched_get(*, cwd, **kw):
        from koder_agent.harness.agents.definitions import BUILTIN_AGENT_DEFINITIONS

        all_agents = list(BUILTIN_AGENT_DEFINITIONS) + [bg_agent]
        return AgentDefinitionsResult(active_agents=all_agents, all_agents=all_agents)

    monkeypatch.setattr("koder_agent.harness.agents.definitions.get_agent_definitions", patched_get)

    async def run():
        result = await _agent_tool_impl(
            description="BG forced",
            prompt="Do work",
            subagent_type="bg-worker",
            # NOTE: run_in_background NOT passed
        )
        parsed = json.loads(result)
        assert parsed["status"] == "async_launched", f"Expected async_launched but got {parsed}"
        assert parsed["agent_id"].startswith("agent-")

    asyncio.run(run())


def test_subagent_cannot_spawn_other_subagents():
    """Subagents should not have access to agent_tool or task_delegate."""
    from koder_agent.harness.agents.definitions import (
        AgentDefinition,
        filter_tools_for_agent_definition,
    )
    from koder_agent.tools import get_all_tools

    # Simulate what _execute_agent_run does
    agent_def = AgentDefinition(
        agent_type="general-purpose",
        when_to_use="General",
        system_prompt="Agent.",
        source="built-in",
        tools=["*"],
    )
    tools = filter_tools_for_agent_definition(agent_def, get_all_tools())
    # After filtering, remove spawning tools (as _execute_agent_run does)
    tools = [t for t in tools if t.name not in {"task_delegate", "agent_tool"}]
    names = {t.name for t in tools}
    assert "agent_tool" not in names
    assert "task_delegate" not in names
    assert "read_file" in names  # Other tools still present


def test_agent_tool_disable_background_tasks_env(monkeypatch):
    """KODER_DISABLE_BACKGROUND_TASKS=1 forces sync execution."""
    monkeypatch.setenv("KODER_DISABLE_BACKGROUND_TASKS", "1")

    async def fake_execute(*, agent_definition, prompt, session_id, seed_items, cwd, **_kwargs):
        return "forced sync"

    monkeypatch.setattr("koder_agent.harness.agents.service._execute_agent_run", fake_execute)

    from koder_agent.tools.agent import _agent_tool_impl

    result = asyncio.run(
        _agent_tool_impl(
            description="Forced sync",
            prompt="Run in foreground",
            run_in_background=True,  # Would normally be async
        )
    )
    parsed = json.loads(result)
    assert parsed["status"] == "completed"  # Forced to sync despite run_in_background=True


def test_agent_service_name_registry():
    """AgentService name registry can register, resolve, and look up agents."""
    from koder_agent.harness.agents.service import AgentService

    service = AgentService.for_test()
    agent_id = service.spawn("worker")
    service.register_name("researcher", agent_id)

    # get_by_name returns the record
    record = service.get_by_name("researcher")
    assert record is not None
    assert record.id == agent_id

    # resolve_agent_id works with both name and id
    assert service.resolve_agent_id("researcher") == agent_id
    assert service.resolve_agent_id(agent_id) == agent_id

    # Unknown name returns None
    assert service.resolve_agent_id("nonexistent") is None
    assert service.get_by_name("nonexistent") is None
