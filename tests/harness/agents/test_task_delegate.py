import asyncio
import re
import sys
import types
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

from koder_agent.harness.agents.definitions import AgentDefinition
from koder_agent.tools.task import (
    DEFAULT_TASK_DELEGATE_MAX_CONCURRENCY,
    HARD_MAX_TASK_DELEGATE_BATCH_SIZE,
    TASK_DELEGATE_AGGREGATE_MAX_CHARS,
    TASK_DELEGATE_CHILD_RESULT_MAX_CHARS,
    TaskDelegateModel,
    TaskModel,
    _bounded_report_text,
    _task_delegate_impl,
    task_delegate,
)


def _tasks(count):
    return [
        TaskModel(description=f"task-{index}", prompt=f"prompt-{index}") for index in range(count)
    ]


def _array_schema():
    tasks_schema = task_delegate.params_json_schema["properties"]["tasks"]
    return next(branch for branch in tasks_schema["anyOf"] if branch.get("type") == "array")


def _patch_delegate_runtime(monkeypatch, fake_run, *, cleanup=None) -> None:
    async def fake_create_dev_agent(*args, **kwargs):
        return SimpleNamespace(_koder_mcp_servers=[])

    monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
    monkeypatch.setattr("agents.Runner.run", fake_run)
    if cleanup is not None:
        monkeypatch.setattr(
            "koder_agent.harness.agents.service._cleanup_agent_mcp_servers",
            cleanup,
        )


def test_task_delegate_schema_advertises_default_batch_limit(monkeypatch):
    monkeypatch.delenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", raising=False)

    from koder_agent.tools.task import refresh_task_delegate_schema_limit

    refresh_task_delegate_schema_limit()

    assert _array_schema()["maxItems"] == 6


def test_task_delegate_schema_reflects_env_override(monkeypatch):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "2")

    from koder_agent.tools.task import refresh_task_delegate_schema_limit

    try:
        refresh_task_delegate_schema_limit()
        assert _array_schema()["maxItems"] == 2
    finally:
        monkeypatch.delenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", raising=False)
        refresh_task_delegate_schema_limit()


def test_task_delegate_schema_strictly_rejects_decimal_env(monkeypatch):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "3.0")

    from koder_agent.tools.task import refresh_task_delegate_schema_limit

    try:
        with pytest.raises(ValueError, match="expected an integer between 1 and 32"):
            refresh_task_delegate_schema_limit()
        assert _array_schema()["maxItems"] == 6
    finally:
        monkeypatch.delenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", raising=False)
        refresh_task_delegate_schema_limit()


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "not-an-int"),
        ("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "3.0"),
        ("KODER_TASK_DELEGATE_MAX_CONCURRENCY", "not-an-int"),
    ],
)
def test_invalid_task_delegate_env_does_not_break_tool_registry_loading(
    monkeypatch,
    env_name,
    env_value,
):
    monkeypatch.setenv(env_name, env_value)

    from koder_agent.tools import get_all_tools

    tools = get_all_tools()

    assert any(tool.name == "task_delegate" for tool in tools)
    assert _array_schema()["maxItems"] == 6


def test_task_delegate_resolves_config_override(monkeypatch):
    monkeypatch.delenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", raising=False)

    class FakeConfigService:
        def load(self):
            return SimpleNamespace(harness=SimpleNamespace(task_delegate_max_batch_size=4))

    monkeypatch.setattr(
        "koder_agent.harness.config.service.RuntimeConfigService",
        FakeConfigService,
    )

    from koder_agent.tools.task import resolve_task_delegate_max_batch_size

    assert resolve_task_delegate_max_batch_size() == 4


def test_task_delegate_defaults_to_distinct_concurrency_limit(monkeypatch):
    monkeypatch.delenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", raising=False)
    monkeypatch.delenv("KODER_TASK_DELEGATE_MAX_CONCURRENCY", raising=False)

    class FakeConfigService:
        def load(self):
            return SimpleNamespace(
                harness=SimpleNamespace(
                    task_delegate_max_batch_size=6,
                    task_delegate_max_concurrency=DEFAULT_TASK_DELEGATE_MAX_CONCURRENCY,
                )
            )

    monkeypatch.setattr(
        "koder_agent.harness.config.service.RuntimeConfigService",
        FakeConfigService,
    )

    from koder_agent.tools.task import resolve_task_delegate_limits

    limits = resolve_task_delegate_limits()

    assert limits.max_batch_size == 6
    assert limits.max_concurrency == DEFAULT_TASK_DELEGATE_MAX_CONCURRENCY


def test_task_delegate_rejects_concurrency_above_batch(monkeypatch):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "3")
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_CONCURRENCY", "4")

    output = asyncio.run(_task_delegate_impl(TaskModel(description="task", prompt="prompt")))

    assert "KODER_TASK_DELEGATE_MAX_CONCURRENCY" in output
    assert "must be less than or equal to" in output


def test_task_delegate_model_rejects_batches_beyond_hard_ceiling():
    with pytest.raises(ValidationError):
        TaskDelegateModel(tasks=_tasks(HARD_MAX_TASK_DELEGATE_BATCH_SIZE + 1))


def test_task_delegate_direct_call_rejects_oversized_batch(monkeypatch):
    monkeypatch.delenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", raising=False)
    run_calls = 0

    async def fake_run(*args, **kwargs):
        nonlocal run_calls
        run_calls += 1
        return SimpleNamespace(final_output="unexpected")

    monkeypatch.setattr("agents.Runner.run", fake_run)

    output = asyncio.run(_task_delegate_impl(_tasks(7)))

    assert "exceeds the configured maximum of 6" in output
    assert run_calls == 0


@pytest.mark.parametrize(
    "value",
    ["0", "3.0", "not-an-int", str(HARD_MAX_TASK_DELEGATE_BATCH_SIZE + 1)],
)
def test_task_delegate_rejects_invalid_env_configuration(monkeypatch, value):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", value)

    output = asyncio.run(_task_delegate_impl(TaskModel(description="task", prompt="prompt")))

    assert "KODER_TASK_DELEGATE_MAX_BATCH_SIZE" in output
    assert "invalid" in output.lower()


@pytest.mark.parametrize("value", ["0", "2.0", "not-an-int", "33"])
def test_task_delegate_rejects_invalid_concurrency_env_configuration(monkeypatch, value):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_CONCURRENCY", value)

    output = asyncio.run(_task_delegate_impl(TaskModel(description="task", prompt="prompt")))

    assert "KODER_TASK_DELEGATE_MAX_CONCURRENCY" in output
    assert "invalid" in output.lower()


def test_task_delegate_limits_measured_concurrency(monkeypatch):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "6")
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_CONCURRENCY", "2")
    active = 0
    maximum_active = 0
    release = asyncio.Event()

    async def fake_create_dev_agent(*args, **kwargs):
        return SimpleNamespace(_koder_mcp_servers=[])

    async def fake_run(agent, prompt, **kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        if maximum_active == 2:
            release.set()
        await release.wait()
        await asyncio.sleep(0)
        active -= 1
        return SimpleNamespace(final_output=prompt)

    monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
    monkeypatch.setattr("agents.Runner.run", fake_run)

    output = asyncio.run(_task_delegate_impl(_tasks(6)))

    assert maximum_active == 2
    assert output.index("prompt-0") < output.index("prompt-1")
    assert "prompt-5" in output


def test_task_delegate_preserves_input_order_mixed_results_and_cleans_resources(monkeypatch):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "3")
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_CONCURRENCY", "3")
    cleaned = []
    seen_max_turns = []

    class Server:
        def __init__(self, name):
            self.name = name

        async def cleanup(self):
            cleaned.append(self.name)

    async def fake_create_dev_agent(*args, **kwargs):
        name = kwargs["name"]
        return SimpleNamespace(name=name, _koder_mcp_servers=[Server(name)])

    async def fake_run(agent, prompt, **kwargs):
        seen_max_turns.append(kwargs["max_turns"])
        if prompt == "prompt-0":
            await asyncio.sleep(0.02)
        elif prompt == "prompt-1":
            raise RuntimeError("child failed")
        return SimpleNamespace(final_output=f"result-{prompt}")

    monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
    monkeypatch.setattr("agents.Runner.run", fake_run)

    output = asyncio.run(_task_delegate_impl(_tasks(3)))

    first = output.index("## Task 1: task-0")
    second = output.index("## Task 2: task-1")
    third = output.index("## Task 3: task-2")
    assert first < second < third
    assert "result-prompt-0" in output
    assert "Error: child failed" in output
    assert "result-prompt-2" in output
    assert len(cleaned) == 3
    assert len(seen_max_turns) == 3
    assert len(set(seen_max_turns)) == 1


@pytest.mark.asyncio
async def test_task_delegate_routes_child_tools_to_parent_display(monkeypatch, capsys):
    from koder_agent.core.display_context import (
        subagent_display_scope,
        tool_display_call_scope,
    )

    events = []

    async def fake_run(agent, prompt, **kwargs):
        hooks = kwargs["hooks"]
        child = SimpleNamespace(name="child")
        tool = SimpleNamespace(name="read_file")
        await hooks.on_agent_start(None, child)
        await hooks.on_tool_start(None, child, tool)
        await hooks.on_tool_end(None, child, tool, "contents")
        return SimpleNamespace(final_output="done")

    _patch_delegate_runtime(monkeypatch, fake_run)

    with (
        subagent_display_scope(events.append),
        tool_display_call_scope("task_delegate", "call-parent"),
    ):
        output = await _task_delegate_impl(
            TaskModel(description="Inspect renderer", prompt="inspect")
        )

    assert "done" in output
    assert capsys.readouterr().out == ""
    assert [event.kind for event in events] == [
        "started",
        "tool_started",
        "tool_finished",
        "completed",
    ]
    assert all(event.identity.parent_call_id == "call-parent" for event in events)


@pytest.mark.asyncio
async def test_task_delegate_display_sink_failure_does_not_change_result(monkeypatch):
    from koder_agent.core.display_context import subagent_display_scope

    async def fake_run(*args, **kwargs):
        return SimpleNamespace(final_output="child succeeded")

    def failing_sink(event):
        raise RuntimeError("display sink failed")

    _patch_delegate_runtime(monkeypatch, fake_run)

    with subagent_display_scope(failing_sink):
        output = await _task_delegate_impl(
            TaskModel(description="Inspect renderer", prompt="inspect")
        )

    assert "child succeeded" in output
    assert "display sink failed" not in output


@pytest.mark.asyncio
async def test_task_delegate_preserves_child_error_when_cleanup_also_fails(monkeypatch):
    async def fake_run(*args, **kwargs):
        raise RuntimeError("child failed")

    async def failing_cleanup(*args, **kwargs):
        raise RuntimeError("cleanup failed")

    _patch_delegate_runtime(monkeypatch, fake_run, cleanup=failing_cleanup)

    output = await _task_delegate_impl(TaskModel(description="Inspect child", prompt="inspect"))

    assert "Error: child failed" in output
    assert "cleanup failed" not in output


@pytest.mark.asyncio
async def test_task_delegate_converts_result_formatting_failure_to_child_error(monkeypatch):
    class UnprintableResult:
        def __str__(self):
            raise RuntimeError("result formatting failed")

    async def fake_run(*args, **kwargs):
        return SimpleNamespace(final_output=UnprintableResult())

    _patch_delegate_runtime(monkeypatch, fake_run)

    output = await _task_delegate_impl(TaskModel(description="Inspect child", prompt="inspect"))

    assert "Error: result formatting failed" in output


def test_task_delegate_parent_cancellation_cancels_running_and_queued_children(monkeypatch):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "3")
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_CONCURRENCY", "1")
    started = []
    cancelled = []
    cleaned = []

    class Server:
        def __init__(self, name):
            self.name = name

        async def cleanup(self):
            cleaned.append(self.name)

    async def fake_create_dev_agent(*args, **kwargs):
        name = kwargs["name"]
        return SimpleNamespace(name=name, _koder_mcp_servers=[Server(name)])

    async def scenario():
        running = asyncio.Event()
        never = asyncio.Event()

        async def fake_run(agent, prompt, **kwargs):
            started.append(prompt)
            running.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                cancelled.append(prompt)
                raise

        monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
        monkeypatch.setattr("agents.Runner.run", fake_run)

        parent = asyncio.create_task(_task_delegate_impl(_tasks(3)))
        await asyncio.wait_for(running.wait(), timeout=1)
        parent.cancel()
        with pytest.raises(asyncio.CancelledError):
            await parent
        await asyncio.sleep(0)

        leaked = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert leaked == []

    asyncio.run(scenario())

    assert started == ["prompt-0"]
    assert cancelled == ["prompt-0"]
    assert len(cleaned) == 1


def test_task_delegate_parent_recancellation_waits_for_slow_child_cleanup(monkeypatch):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "3")
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_CONCURRENCY", "1")
    started = []
    cancelled = []
    cleanup_calls = 0
    cleanup_completed = 0
    active_lifecycles = 0

    async def scenario():
        nonlocal active_lifecycles, cleanup_calls, cleanup_completed
        running = asyncio.Event()
        never = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()

        class Server:
            async def cleanup(self):
                nonlocal active_lifecycles, cleanup_calls, cleanup_completed
                cleanup_calls += 1
                cleanup_started.set()
                await cleanup_release.wait()
                await asyncio.sleep(0)
                cleanup_completed += 1
                active_lifecycles -= 1

        async def fake_create_dev_agent(*args, **kwargs):
            nonlocal active_lifecycles
            active_lifecycles += 1
            return SimpleNamespace(_koder_mcp_servers=[Server()])

        async def fake_run(agent, prompt, **kwargs):
            started.append(prompt)
            running.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                cancelled.append(prompt)
                raise

        monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
        monkeypatch.setattr("agents.Runner.run", fake_run)

        parent = asyncio.create_task(_task_delegate_impl(_tasks(3)))
        await asyncio.wait_for(running.wait(), timeout=1)
        parent.cancel("stop delegation")
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)

        parent.cancel("repeat cancellation")
        await asyncio.sleep(0)
        assert not parent.done()
        assert active_lifecycles == 1
        assert started == ["prompt-0"]

        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError) as cancellation:
            await asyncio.wait_for(parent, timeout=1)
        assert cancellation.value.args == ("stop delegation",)

        leaked_children = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and not task.done()
            and task.get_name().startswith(("task-delegate:", "mcp-server-cleanup"))
        ]
        assert leaked_children == []

    asyncio.run(scenario())

    assert started == ["prompt-0"]
    assert cancelled == ["prompt-0"]
    assert cleanup_calls == 1
    assert cleanup_completed == 1
    assert active_lifecycles == 0


def test_task_delegate_preserves_cancellation_when_cleanup_fails(monkeypatch):
    running = asyncio.Event()
    never = asyncio.Event()

    async def fake_run(*args, **kwargs):
        running.set()
        await never.wait()

    async def failing_cleanup(*args, **kwargs):
        raise RuntimeError("cleanup failed")

    _patch_delegate_runtime(monkeypatch, fake_run, cleanup=failing_cleanup)

    async def scenario():
        parent = asyncio.create_task(
            _task_delegate_impl(TaskModel(description="wait", prompt="wait"))
        )
        await asyncio.wait_for(running.wait(), timeout=1)
        parent.cancel("stop delegation")
        with pytest.raises(asyncio.CancelledError) as cancellation:
            await parent
        assert cancellation.value.args == ("stop delegation",)

    asyncio.run(scenario())


def test_task_delegate_can_target_named_agent(monkeypatch):
    monkeypatch.delenv("KODER_SUBAGENT_MODEL", raising=False)
    monkeypatch.setenv("KODER_MAX_TURNS", "99")
    seen_model_overrides = []
    seen_todo_identities = []
    seen_max_turns = []

    async def fake_create_dev_agent(*args, **kwargs):
        seen_model_overrides.append(kwargs.get("model_override"))
        return object()

    class _Result:
        final_output = "delegated result"

    async def fake_run(*args, **kwargs):
        from koder_agent.tools.todo import get_todo_store

        seen_todo_identities.append(get_todo_store().identity)
        seen_max_turns.append(kwargs["max_turns"])
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
                        max_turns=2,
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
    assert len(seen_todo_identities) == 1
    assert seen_todo_identities[0].session_id == "__direct__"
    assert seen_todo_identities[0].agent_id == "reviewer"
    assert seen_todo_identities[0].run_id.startswith("task-")

    from koder_agent.tools.todo import get_todo_store_or_none

    assert get_todo_store_or_none() is None
    assert seen_max_turns == [2]


def test_task_delegate_bounds_child_results_and_total_report(monkeypatch):
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_BATCH_SIZE", "6")
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_CONCURRENCY", "2")

    async def fake_create_dev_agent(*args, **kwargs):
        return SimpleNamespace(_koder_mcp_servers=[])

    async def fake_run(agent, prompt, **kwargs):
        index = int(prompt.rsplit("-", 1)[1])
        if index == 2:
            raise RuntimeError("failure-" + ("e" * (TASK_DELEGATE_CHILD_RESULT_MAX_CHARS * 2)))
        return SimpleNamespace(
            final_output=f"start-{index}-"
            + (str(index) * (TASK_DELEGATE_CHILD_RESULT_MAX_CHARS * 2))
            + f"-end-{index}"
        )

    monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
    monkeypatch.setattr("agents.Runner.run", fake_run)

    output = asyncio.run(_task_delegate_impl(_tasks(6)))

    assert len(output) <= TASK_DELEGATE_AGGREGATE_MAX_CHARS
    assert "task result truncated: omitted" in output
    assert "Error: failure-" in output
    positions = [output.index(f"## Task {index}: task-{index - 1}") for index in range(1, 7)]
    assert positions == sorted(positions)


def test_bounded_task_result_reports_exact_omitted_character_count():
    source = "start-" + ("x" * (TASK_DELEGATE_CHILD_RESULT_MAX_CHARS * 2)) + "-end"

    bounded = _bounded_report_text(
        source,
        TASK_DELEGATE_CHILD_RESULT_MAX_CHARS,
        label="task result",
    )

    marker = re.search(
        r"\n\.\.\.\[task result truncated: omitted (\d+) of (\d+) characters\]\.\.\.\n",
        bounded,
    )
    assert marker is not None
    omitted, original = (int(value) for value in marker.groups())
    assert len(bounded) == TASK_DELEGATE_CHILD_RESULT_MAX_CHARS
    assert original == len(source)
    assert omitted == len(source) - (len(bounded) - len(marker.group(0)))
    assert bounded[: marker.start()] == source[: marker.start()]
    assert bounded[marker.end() :] == source[-len(bounded[marker.end() :]) :]


def test_task_delegate_exact_aggregate_budget_retains_all_ordered_sections(monkeypatch):
    monkeypatch.setenv(
        "KODER_TASK_DELEGATE_MAX_BATCH_SIZE",
        str(HARD_MAX_TASK_DELEGATE_BATCH_SIZE),
    )
    monkeypatch.setenv("KODER_TASK_DELEGATE_MAX_CONCURRENCY", "4")

    async def fake_create_dev_agent(*args, **kwargs):
        return SimpleNamespace(_koder_mcp_servers=[])

    async def fake_run(agent, prompt, **kwargs):
        return SimpleNamespace(final_output=prompt + ("x" * TASK_DELEGATE_CHILD_RESULT_MAX_CHARS))

    monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
    monkeypatch.setattr("agents.Runner.run", fake_run)

    output = asyncio.run(_task_delegate_impl(_tasks(HARD_MAX_TASK_DELEGATE_BATCH_SIZE)))

    assert len(output) == TASK_DELEGATE_AGGREGATE_MAX_CHARS
    positions = [
        output.index(f"## Task {index}: task-{index - 1}")
        for index in range(1, HARD_MAX_TASK_DELEGATE_BATCH_SIZE + 1)
    ]
    assert positions == sorted(positions)
    assert output.count("task result truncated: omitted") == HARD_MAX_TASK_DELEGATE_BATCH_SIZE


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


@pytest.mark.parametrize("failure_boundary", [None, "permission", "runner"])
def test_task_delegate_closes_agent_owner_on_every_exit(monkeypatch, failure_boundary):
    from koder_agent.mcp import MCPServerSet

    class Server:
        name = "task-owned"

        def __init__(self):
            self.cleanup_count = 0

        async def cleanup(self):
            self.cleanup_count += 1

    server = Server()
    owner = MCPServerSet([server])
    agent = types.SimpleNamespace(mcp_servers=[], _koder_mcp_servers=owner)

    async def fake_create_dev_agent(*args, **kwargs):
        return agent

    async def fake_run(*args, **kwargs):
        if failure_boundary == "runner":
            raise RuntimeError("runner failed")
        return types.SimpleNamespace(final_output="delegated")

    monkeypatch.setattr("koder_agent.agentic.create_dev_agent", fake_create_dev_agent)
    monkeypatch.setattr("agents.Runner.run", fake_run)
    if failure_boundary == "permission":
        monkeypatch.setattr(
            "koder_agent.tools.permission_context.subagent_permission_scope",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("permission failed")),
        )

    output = asyncio.run(_task_delegate_impl(TaskModel(description="d", prompt="p")))

    if failure_boundary is None:
        assert "delegated" in output
    else:
        assert "Error:" in output
    assert server.cleanup_count == 1
    assert agent._koder_mcp_servers is None
