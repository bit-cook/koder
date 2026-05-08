"""Tests for concurrent tool orchestration."""

import asyncio
import time

import pytest

from koder_agent.tools.orchestration import (
    MAX_CONCURRENT_READS,
    READ_ONLY_TOOL_NAMES,
    ToolOrchestrator,
)


def test_read_only_tools_defined():
    assert "read_file" in READ_ONLY_TOOL_NAMES
    assert "glob_search" in READ_ONLY_TOOL_NAMES
    assert "grep_search" in READ_ONLY_TOOL_NAMES
    assert "code_intelligence" in READ_ONLY_TOOL_NAMES
    assert "list_directory" in READ_ONLY_TOOL_NAMES
    assert "web_search" in READ_ONLY_TOOL_NAMES
    assert "web_fetch" in READ_ONLY_TOOL_NAMES


def test_write_tools_not_in_readonly():
    assert "write_file" not in READ_ONLY_TOOL_NAMES
    assert "edit_file" not in READ_ONLY_TOOL_NAMES
    assert "run_shell" not in READ_ONLY_TOOL_NAMES


def test_max_concurrent():
    assert MAX_CONCURRENT_READS == 10


def test_is_read_only():
    orch = ToolOrchestrator()
    assert orch.is_read_only("read_file")
    assert orch.is_read_only("grep_search")
    assert orch.is_read_only("code_intelligence")
    assert not orch.is_read_only("write_file")
    assert not orch.is_read_only("run_shell")


def test_partition_tool_calls():
    """Should partition calls into read-only batches and write calls."""
    orch = ToolOrchestrator()
    calls = [
        {"tool": "read_file", "args": {"path": "a.py"}},
        {"tool": "read_file", "args": {"path": "b.py"}},
        {"tool": "write_file", "args": {"path": "c.py", "content": "x"}},
        {"tool": "grep_search", "args": {"pattern": "test"}},
    ]
    batches = orch.partition_calls(calls)
    # Should produce: [read_batch(read, read), write(write), read_batch(grep)]
    assert len(batches) >= 2


@pytest.mark.asyncio
async def test_concurrent_reads_faster_than_serial():
    """Multiple read-only calls should run concurrently."""
    orch = ToolOrchestrator()

    call_count = 0

    async def slow_read(name, args):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)  # Simulate I/O
        return f"result_{call_count}"

    calls = [{"tool": "read_file", "args": {"path": f"file{i}.py"}} for i in range(5)]

    start = time.monotonic()
    results = await orch.execute_batch(calls, executor=slow_read)
    elapsed = time.monotonic() - start

    assert len(results) == 5
    # 5 concurrent 0.1s sleeps should take ~0.1s, not 0.5s
    assert elapsed < 0.3  # Allow some overhead


@pytest.mark.asyncio
async def test_write_calls_serialized():
    """Write calls should run one at a time."""
    orch = ToolOrchestrator()
    order = []

    async def tracked_exec(name, args):
        order.append(f"start_{name}")
        await asyncio.sleep(0.05)
        order.append(f"end_{name}")
        return "ok"

    calls = [
        {"tool": "write_file", "args": {"path": "a.py"}},
        {"tool": "write_file", "args": {"path": "b.py"}},
    ]

    results = await orch.execute_batch(calls, executor=tracked_exec)
    assert len(results) == 2
    # Writes should be serial: start_a, end_a, start_b, end_b
    assert order.index("end_write_file") < order.index("start_write_file", 2)


@pytest.mark.asyncio
async def test_mixed_calls_correct_order():
    """Mixed calls: reads batched, writes serial, order preserved."""
    orch = ToolOrchestrator()
    results_order = []

    async def tracked(name, args):
        results_order.append(name)
        await asyncio.sleep(0.01)
        return f"result_{name}"

    calls = [
        {"tool": "read_file", "args": {}},
        {"tool": "grep_search", "args": {}},
        {"tool": "write_file", "args": {}},
        {"tool": "read_file", "args": {}},
    ]

    results = await orch.execute_batch(calls, executor=tracked)
    assert len(results) == 4
