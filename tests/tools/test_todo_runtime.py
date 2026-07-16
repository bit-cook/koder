"""Runtime-scoped todo state regression tests."""

from __future__ import annotations

import asyncio
import json

import pytest

from koder_agent.tools.todo import (
    TodoRuntimeIdentity,
    TodoStore,
    get_todo_store,
    reset_todo_context,
    reset_todo_state_for_tests,
    set_todo_context,
    todo_read,
    todo_write,
)


def _payload(content: str) -> str:
    return json.dumps(
        {
            "todos": [
                {
                    "content": content,
                    "status": "in_progress",
                    "priority": "high",
                    "id": content,
                }
            ]
        }
    )


@pytest.fixture(autouse=True)
def _reset_direct_todos():
    reset_todo_state_for_tests()
    yield
    reset_todo_state_for_tests()


@pytest.mark.asyncio
async def test_direct_tool_invocation_works_inside_explicit_seeded_scope():
    store = TodoStore(TodoRuntimeIdentity.direct())
    token = set_todo_context(store)
    try:
        await todo_write.on_invoke_tool(None, _payload("direct"))
        assert get_todo_store() is store
        assert "direct" in await todo_read.on_invoke_tool(None, "{}")
    finally:
        reset_todo_context(token)


@pytest.mark.asyncio
async def test_unscoped_concurrent_tool_callers_fail_closed_without_shared_state():
    async def invoke(tool, payload):
        return await tool.on_invoke_tool(None, payload)

    results = await asyncio.gather(
        invoke(todo_write, _payload("caller-a")),
        invoke(todo_write, _payload("caller-b")),
        invoke(todo_read, "{}"),
    )
    assert all("explicit runtime identity" in result for result in results)

    with pytest.raises(RuntimeError, match="explicit runtime identity"):
        get_todo_store()


@pytest.mark.asyncio
async def test_session_and_parallel_agent_todos_are_isolated():
    store_a = TodoStore(TodoRuntimeIdentity("session-a", "main", "run-a"))
    store_b = TodoStore(TodoRuntimeIdentity("session-b", "main", "run-b"))

    async def write_and_read(store: TodoStore, content: str) -> str:
        token = set_todo_context(store)
        try:
            await todo_write.on_invoke_tool(None, _payload(content))
            await asyncio.sleep(0)
            return await todo_read.on_invoke_tool(None, "{}")
        finally:
            reset_todo_context(token)

    result_a, result_b = await asyncio.gather(
        write_and_read(store_a, "session A task"),
        write_and_read(store_b, "session B task"),
    )

    assert "session A task" in result_a
    assert "session B task" not in result_a
    assert "session B task" in result_b
    assert "session A task" not in result_b


@pytest.mark.asyncio
async def test_contextvar_propagates_into_sdk_created_task():
    store = TodoStore(TodoRuntimeIdentity("session-sdk", "main", "run-sdk"))
    token = set_todo_context(store)
    try:

        async def sdk_created_tool_task():
            assert get_todo_store() is store
            await todo_write.on_invoke_tool(None, _payload("from SDK task"))

        await asyncio.create_task(sdk_created_tool_task())
    finally:
        reset_todo_context(token)

    assert store.todos[0]["content"] == "from SDK task"


@pytest.mark.asyncio
async def test_cleanup_and_session_switch_reuse_do_not_erase_other_runtime():
    from unittest.mock import AsyncMock, patch

    from koder_agent.core.scheduler import AgentScheduler

    with (
        patch("koder_agent.core.scheduler.get_all_tools", return_value=[]),
        patch("koder_agent.core.scheduler.get_display_hooks"),
        patch("koder_agent.core.scheduler.ApprovalHooks"),
        patch("koder_agent.core.scheduler.EnhancedSQLiteSession") as session_cls,
    ):
        session_cls.return_value.db_path = ":memory:"
        scheduler_a = AgentScheduler(session_id="session-a")
        scheduler_b = AgentScheduler(session_id="session-b")
        scheduler_a.goal_store = AsyncMock()
        scheduler_b.goal_store = AsyncMock()
        scheduler_a.todo_store.todos = [{"content": "A", "status": "pending", "id": "a"}]
        scheduler_b.todo_store.todos = [{"content": "B", "status": "pending", "id": "b"}]

        await scheduler_a.cleanup()
        assert scheduler_b.todo_store.todos[0]["content"] == "B"

        resumed_a = AgentScheduler(session_id="session-a", todo_store=scheduler_a.todo_store)
        assert resumed_a.todo_store.todos[0]["content"] == "A"

        fresh_runtime = AgentScheduler(session_id="session-a")
        assert fresh_runtime.todo_store.todos == []


def test_scheduler_rejects_store_from_different_agent_identity():
    from unittest.mock import patch

    from koder_agent.core.scheduler import AgentScheduler
    from koder_agent.harness.agents.definitions import AgentDefinition

    custom_agent = AgentDefinition(
        agent_type="reviewer",
        when_to_use="Review code",
        system_prompt="Review code.",
        source="test",
    )
    custom_store = TodoStore(TodoRuntimeIdentity("shared-session", "reviewer", "run"))

    with (
        patch("koder_agent.core.scheduler.get_all_tools", return_value=[]),
        patch("koder_agent.core.scheduler.get_display_hooks"),
        patch("koder_agent.core.scheduler.ApprovalHooks"),
        patch("koder_agent.core.scheduler.EnhancedSQLiteSession") as session_cls,
    ):
        session_cls.return_value.db_path = ":memory:"
        with pytest.raises(ValueError, match="session and agent"):
            AgentScheduler(session_id="shared-session", todo_store=custom_store)

        scheduler = AgentScheduler(
            session_id="shared-session",
            agent_definition=custom_agent,
            todo_store=custom_store,
        )
        assert scheduler.todo_store is custom_store
