"""Tests for _reconcile_tool_pairs in AgentScheduler.

M1 fix: after filtering unreplayable items, orphaned function_call or
function_call_output items (whose counterpart was removed) must be stripped
to avoid provider 400 errors.
"""

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.memory.compact import replayable_session_items


class TestReconcileToolPairs:
    """Unit tests for AgentScheduler._reconcile_tool_pairs."""

    def test_paired_items_kept(self):
        """Items with matching call_id on both sides survive."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        assert result == items

    def test_orphaned_call_removed(self):
        """A function_call without a matching function_call_output is dropped."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            # No corresponding function_call_output for call_1
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        assert len(result) == 2
        assert all(item.get("type") != "function_call" for item in result)

    def test_orphaned_output_removed(self):
        """A function_call_output without a matching function_call is dropped."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            # No corresponding function_call for call_2
            {"type": "function_call_output", "call_id": "call_2", "output": "ok"},
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        assert len(result) == 2
        assert all(item.get("type") != "function_call_output" for item in result)

    def test_mixed_paired_and_orphaned(self):
        """Only the orphaned half is removed; paired items stay."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "file_content"},
            {"type": "function_call", "call_id": "call_2", "name": "write_file", "arguments": "{}"},
            # call_2 output was removed by replayable filter
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        # call_2 (orphaned call) should be removed; everything else stays
        assert len(result) == 4
        call_ids = [item.get("call_id") for item in result if item.get("call_id")]
        assert "call_1" in call_ids
        assert "call_2" not in call_ids

    def test_non_tool_items_always_kept(self):
        """Messages and other item types are never touched."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "message", "role": "assistant", "content": "hello"},
            {"type": "reasoning", "content": "thinking..."},
        ]
        result = replayable_session_items(items)
        assert result == items

    def test_empty_list(self):
        """Empty input returns empty output."""
        assert replayable_session_items([]) == []

    def test_items_without_call_id_are_dropped(self):
        """function_call items without call_id cannot be paired; they are dropped."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "name": "bad_item"},  # no call_id
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        assert len(result) == 2
        assert all(item.get("type") != "function_call" for item in result)

    def test_multiple_paired_calls(self):
        """Multiple complete pairs all survive."""
        items = [
            {"type": "function_call", "call_id": "a", "name": "tool_a", "arguments": "{}"},
            {"type": "function_call", "call_id": "b", "name": "tool_b", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "a", "output": "out_a"},
            {"type": "function_call_output", "call_id": "b", "output": "out_b"},
        ]
        result = replayable_session_items(items)
        assert len(result) == 4


@pytest.mark.asyncio
async def test_interrupted_tool_pair_resume_appends_one_synthetic_assistant_marker():
    from koder_agent.core.scheduler import AgentScheduler

    items = [
        {"role": "user", "content": "inspect it"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "read_file",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "contents"},
    ]

    with (
        patch("koder_agent.core.scheduler.get_all_tools", return_value=[]),
        patch("koder_agent.core.scheduler.get_display_hooks"),
        patch("koder_agent.core.scheduler.ApprovalHooks"),
        patch("koder_agent.core.scheduler.EnhancedSQLiteSession") as session_cls,
    ):
        session = AsyncMock()
        session.session_id = "repair-session"
        session.db_path = ":memory:"
        session.summarization_threshold = None

        async def get_items():
            return list(items)

        async def add_items(batch):
            items.extend(batch)

        session.get_items = AsyncMock(side_effect=get_items)
        session.add_items = AsyncMock(side_effect=add_items)
        session.clear_session = AsyncMock()
        session_cls.return_value = session
        scheduler = AgentScheduler(session_id="repair-session")
        scheduler.refresh_context_usage_from_session = AsyncMock()

        await scheduler._repair_unreplayable_session_items()
        await scheduler._repair_unreplayable_session_items()

    session.clear_session.assert_not_awaited()
    session.add_items.assert_awaited_once()
    assert items[:3] == [
        {"role": "user", "content": "inspect it"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "read_file",
            "arguments": "{}",
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "contents"},
    ]
    marker = items[-1]
    assert marker["role"] == "assistant"
    assert "synthetic" in marker["content"].lower()
    assert "interrupted" in marker["content"].lower()
    assert "success" not in marker["content"].lower()
    assert replayable_session_items(items) == items


@pytest.mark.asyncio
async def test_cancelled_stream_defers_marker_until_late_sdk_persistence_settles(monkeypatch):
    from koder_agent.core.scheduler import AgentScheduler

    items = [{"role": "user", "content": "inspect it"}]
    stream_started = asyncio.Event()
    allow_late_persistence = asyncio.Event()

    with (
        patch("koder_agent.core.scheduler.get_all_tools", return_value=[]),
        patch("koder_agent.core.scheduler.get_display_hooks"),
        patch("koder_agent.core.scheduler.ApprovalHooks"),
        patch("koder_agent.core.scheduler.EnhancedSQLiteSession") as session_cls,
    ):
        session = AsyncMock()
        session.session_id = "delayed-repair"
        session.db_path = ":memory:"
        session.summarization_threshold = None

        async def get_items():
            return list(items)

        async def add_items(batch):
            items.extend(batch)

        session.get_items = AsyncMock(side_effect=get_items)
        session.add_items = AsyncMock(side_effect=add_items)
        session.clear_session = AsyncMock()
        session_cls.return_value = session
        scheduler = AgentScheduler(session_id="delayed-repair")

    class DelayedPersistenceResult:
        final_output = None
        late_task = None

        async def stream_events(self):
            stream_started.set()
            try:
                await asyncio.Event().wait()
            finally:

                async def persist_late_items():
                    await allow_late_persistence.wait()
                    await session.add_items(
                        [
                            {
                                "type": "function_call",
                                "call_id": "call_1",
                                "name": "read_file",
                                "arguments": "{}",
                            },
                            {
                                "type": "function_call_output",
                                "call_id": "call_1",
                                "output": "contents",
                            },
                        ]
                    )

                self.late_task = asyncio.create_task(persist_late_items())
            if False:
                yield None

    result = DelayedPersistenceResult()
    scheduler.dev_agent = object()
    scheduler._agent_initialized = True
    scheduler._migration_done = True
    scheduler._capture_usage = AsyncMock()
    scheduler._refresh_magic_docs_after_turn = AsyncMock()
    scheduler.refresh_context_usage_from_session = AsyncMock()
    scheduler.goal_runtime = AsyncMock()
    scheduler.goal_runtime.next_continuation_prompt = AsyncMock(return_value=None)
    monkeypatch.setattr("koder_agent.core.scheduler.get_turn_timeout", lambda: 0)
    monkeypatch.setattr(
        "koder_agent.core.scheduler.Runner.run_streamed",
        lambda *_args, **_kwargs: result,
    )

    task = asyncio.create_task(
        scheduler.handle_stream_json("continue", on_event=lambda _event: None)
    )
    await stream_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert items == [{"role": "user", "content": "inspect it"}]
    allow_late_persistence.set()
    await result.late_task
    assert items[-1]["type"] == "function_call_output"

    await scheduler._repair_unreplayable_session_items()
    await scheduler._repair_unreplayable_session_items()

    assert items[-1]["role"] == "assistant"
    assert "synthetic interruption marker" in items[-1]["content"].lower()
    assert (
        sum(item.get("content") == items[-1]["content"] for item in items if isinstance(item, dict))
        == 1
    )


@pytest.mark.asyncio
async def test_immediate_next_turn_waits_for_cancelled_sdk_persistence_before_repair(monkeypatch):
    from koder_agent.core.scheduler import AgentScheduler

    items = [{"role": "user", "content": "inspect it"}]
    stream_started = asyncio.Event()
    allow_late_persistence = asyncio.Event()
    runner_snapshots = []

    with (
        patch("koder_agent.core.scheduler.get_all_tools", return_value=[]),
        patch("koder_agent.core.scheduler.get_display_hooks"),
        patch("koder_agent.core.scheduler.ApprovalHooks"),
        patch("koder_agent.core.scheduler.EnhancedSQLiteSession") as session_cls,
    ):
        session = AsyncMock()
        session.session_id = "immediate-next-turn"
        session.db_path = ":memory:"
        session.summarization_threshold = None

        async def get_items():
            return list(items)

        async def add_items(batch):
            items.extend(batch)

        session.get_items = AsyncMock(side_effect=get_items)
        session.add_items = AsyncMock(side_effect=add_items)
        session.clear_session = AsyncMock()
        session_cls.return_value = session
        scheduler = AgentScheduler(session_id="immediate-next-turn")

    class CancelledResult:
        final_output = None
        run_loop_task = None

        async def stream_events(self):
            stream_started.set()
            try:
                await asyncio.Event().wait()
            finally:

                async def persist_late_items():
                    await allow_late_persistence.wait()
                    await session.add_items(
                        [
                            {
                                "type": "function_call",
                                "call_id": "call_1",
                                "name": "read_file",
                                "arguments": "{}",
                            },
                            {
                                "type": "function_call_output",
                                "call_id": "call_1",
                                "output": "contents",
                            },
                        ]
                    )

                self.run_loop_task = asyncio.create_task(persist_late_items())
            if False:
                yield None

    class CompletedResult:
        final_output = "done"
        run_loop_task = None

        async def stream_events(self):
            if False:
                yield None

    cancelled_result = CancelledResult()
    completed_result = CompletedResult()
    results = iter([cancelled_result, completed_result])

    def run_streamed(*_args, **_kwargs):
        runner_snapshots.append(list(items))
        return next(results)

    scheduler.dev_agent = object()
    scheduler._agent_initialized = True
    scheduler._migration_done = True
    scheduler._capture_usage = AsyncMock()
    scheduler._refresh_magic_docs_after_turn = AsyncMock()
    scheduler.refresh_context_usage_from_session = AsyncMock()
    scheduler.goal_runtime = AsyncMock()
    scheduler.goal_runtime.next_continuation_prompt = AsyncMock(return_value=None)
    monkeypatch.setattr("koder_agent.core.scheduler.get_turn_timeout", lambda: 0)
    monkeypatch.setattr("koder_agent.core.scheduler.Runner.run_streamed", run_streamed)

    cancelled_turn = asyncio.create_task(
        scheduler.handle_stream_json("continue", on_event=lambda _event: None)
    )
    await stream_started.wait()
    cancelled_turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_turn

    next_turn = asyncio.create_task(
        scheduler.handle_stream_json("next", on_event=lambda _event: None)
    )
    await asyncio.sleep(0)

    assert not next_turn.done()
    assert len(runner_snapshots) == 1
    assert items == [{"role": "user", "content": "inspect it"}]

    allow_late_persistence.set()
    assert await next_turn == "done"

    assert len(runner_snapshots) == 2
    next_runner_history = runner_snapshots[1]
    assert next_runner_history[-1]["role"] == "assistant"
    assert "synthetic interruption marker" in next_runner_history[-1]["content"].lower()
    assert [item.get("type") for item in next_runner_history[-3:-1]] == [
        "function_call",
        "function_call_output",
    ]


@pytest.mark.asyncio
async def test_atomic_repair_failure_does_not_restore_twice_or_append_marker():
    from koder_agent.core.scheduler import AgentScheduler

    original_items = [
        {"role": "user", "content": "inspect it"},
        {
            "type": "function_call",
            "call_id": "orphan",
            "name": "read_file",
            "arguments": "{}",
        },
    ]

    class AtomicSession:
        session_id = "atomic-repair-failure"
        db_path = ":memory:"
        summarization_threshold = None

        def __init__(self):
            self.items = list(original_items)
            self.replace_calls = 0
            self.clear_calls = 0
            self.add_calls = 0

        async def get_items(self):
            return list(self.items)

        async def replace_items(self, _items):
            self.replace_calls += 1
            raise RuntimeError("atomic replacement rolled back")

        async def clear_session(self):
            self.clear_calls += 1

        async def add_items(self, _items):
            self.add_calls += 1

        def close(self):
            return None

    session = AtomicSession()
    with (
        patch("koder_agent.core.scheduler.get_all_tools", return_value=[]),
        patch("koder_agent.core.scheduler.get_display_hooks"),
        patch("koder_agent.core.scheduler.ApprovalHooks"),
        patch("koder_agent.core.scheduler.EnhancedSQLiteSession", return_value=session),
    ):
        scheduler = AgentScheduler(session_id=session.session_id)

    scheduler.refresh_context_usage_from_session = AsyncMock()
    await scheduler._repair_unreplayable_session_items()

    assert session.replace_calls == 1
    assert session.clear_calls == 0
    assert session.add_calls == 0
    assert session.items == original_items
    scheduler.refresh_context_usage_from_session.assert_not_awaited()
    await scheduler.cleanup()


@pytest.mark.asyncio
async def test_cancelled_stream_settlement_then_atomic_repair_appends_marker_once():
    from koder_agent.core.scheduler import AgentScheduler

    class AtomicSession:
        session_id = "atomic-repair-after-settlement"
        db_path = ":memory:"
        summarization_threshold = None

        def __init__(self):
            self.items = [{"role": "user", "content": "inspect it"}]
            self.replace_calls = 0

        async def get_items(self):
            return list(self.items)

        async def replace_items(self, items):
            self.replace_calls += 1
            self.items = list(items)

        async def add_items(self, items):
            self.items.extend(items)

        def close(self):
            return None

    session = AtomicSession()
    with (
        patch("koder_agent.core.scheduler.get_all_tools", return_value=[]),
        patch("koder_agent.core.scheduler.get_display_hooks"),
        patch("koder_agent.core.scheduler.ApprovalHooks"),
        patch("koder_agent.core.scheduler.EnhancedSQLiteSession", return_value=session),
    ):
        scheduler = AgentScheduler(session_id=session.session_id)

    release_late_persistence = asyncio.Event()

    async def persist_late_tail():
        await release_late_persistence.wait()
        session.items.extend(
            [
                {
                    "type": "function_call",
                    "call_id": "complete",
                    "name": "read_file",
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "complete",
                    "output": "contents",
                },
                {
                    "type": "function_call",
                    "call_id": "orphan",
                    "name": "write_file",
                    "arguments": "{}",
                },
            ]
        )

    settlement = asyncio.create_task(persist_late_tail())
    scheduler._cancelled_stream_settlement = settlement
    waiter = asyncio.create_task(scheduler._await_cancelled_stream_settlement())
    await asyncio.sleep(0)
    assert not waiter.done()
    release_late_persistence.set()
    await waiter

    scheduler.refresh_context_usage_from_session = AsyncMock()
    await scheduler._repair_unreplayable_session_items()
    await scheduler._repair_unreplayable_session_items()

    assert session.replace_calls == 1
    assert [item.get("call_id") for item in session.items if item.get("call_id")] == [
        "complete",
        "complete",
    ]
    markers = [
        item
        for item in session.items
        if isinstance(item, dict)
        and isinstance(item.get("content"), str)
        and "synthetic interruption marker" in item["content"].lower()
    ]
    assert len(markers) == 1
    await scheduler.cleanup()
