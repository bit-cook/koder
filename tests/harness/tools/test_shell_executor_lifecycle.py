# ruff: noqa: E402
"""Lifecycle tests for the harness shell executor.

Covers:
- bounded background output buffer (memory does not grow unbounded)
- index-based reader correctness after left-eviction
- process-group termination kills grandchildren on POSIX
"""

import asyncio
import os
import signal
import sys
import time
import types
from pathlib import Path

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.tools import shell_executor
from koder_agent.harness.tools.shell_executor import (
    IS_WINDOWS,
    BackgroundProcess,
    BackgroundProcessManager,
    execute_shell_command,
)


class _FakeProcess:
    """Minimal stand-in so BackgroundProcess can be built without a real proc."""

    returncode = None
    pid = None


def test_background_buffer_is_bounded(monkeypatch):
    """output_lines never exceeds the configured cap even with many appends."""
    monkeypatch.setenv("KODER_BG_SHELL_MAX_LINES", "50")

    shell = BackgroundProcess(
        shell_id="cap",
        command="noisy",
        process=_FakeProcess(),
        start_time=0.0,
    )
    assert shell.output_lines.maxlen == 50

    for i in range(1000):
        shell.add_output(f"line-{i}")

    # Buffer stays bounded; only the most recent lines are retained.
    assert len(shell.output_lines) == 50
    assert shell.output_lines[-1] == "line-999"
    assert shell.output_lines[0] == "line-950"
    # But the monotonic counter reflects every append.
    assert shell._total_appended == 1000


def test_get_new_output_after_eviction(monkeypatch):
    """The index-based reader stays correct after old lines are evicted.

    It must not crash and must not re-emit already-read lines; evicted lines are
    silently skipped rather than raising or duplicating.
    """
    monkeypatch.setenv("KODER_BG_SHELL_MAX_LINES", "10")

    shell = BackgroundProcess(
        shell_id="evict",
        command="noisy",
        process=_FakeProcess(),
        start_time=0.0,
    )

    for i in range(5):
        shell.add_output(f"line-{i}")

    first = shell.get_new_output()
    assert first == [f"line-{i}" for i in range(5)]

    # Append far more than maxlen so the reader's last_read_index points at
    # lines that have since been evicted.
    for i in range(5, 105):
        shell.add_output(f"line-{i}")

    second = shell.get_new_output()
    # Only the retained tail is returned, no crash, no re-read of line-0..4.
    assert second == [f"line-{i}" for i in range(95, 105)]
    assert len(second) == 10

    # A follow-up read with no new appends yields nothing.
    assert shell.get_new_output() == []


def test_get_new_output_accepts_list_seed():
    """BackgroundProcess still accepts a plain list and coerces it to a deque."""
    shell = BackgroundProcess(
        shell_id="seed",
        command="c",
        process=_FakeProcess(),
        start_time=0.0,
        output_lines=["a", "b", "c"],
    )
    import collections

    assert isinstance(shell.output_lines, collections.deque)
    assert shell._total_appended == 3
    assert shell.get_new_output() == ["a", "b", "c"]


@pytest.mark.skipif(IS_WINDOWS, reason="process-group semantics are POSIX-only")
def test_terminate_kills_child_process_group():
    """terminate() reaps a background shell whose wrapper spawns a child group."""

    async def _run():
        # Parent shell sleeps; a backgrounded grandchild sleeps longer. Because
        # the wrapper is a session leader (start_new_session=True), killing its
        # group must take the grandchild down too.
        result = await execute_shell_command(
            "sleep 30 & echo started; wait",
            run_in_background=True,
        )
        shell_id = result.shell_id
        assert shell_id is not None
        shell = BackgroundProcessManager.get(shell_id)
        assert shell is not None
        pid = shell.process.pid

        # Give the wrapper a moment to become a group leader.
        await asyncio.sleep(0.2)
        pgid = os.getpgid(pid)

        terminated = await BackgroundProcessManager.terminate(shell_id)
        assert terminated.status == "terminated"
        assert shell_id not in BackgroundProcessManager.list_ids()

        # The wrapper process is dead.
        assert shell.process.returncode is not None

        # The whole group is gone: signalling it must raise ProcessLookupError.
        await asyncio.sleep(0.1)
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)

    asyncio.run(_run())


@pytest.mark.skipif(IS_WINDOWS, reason="process-group semantics are POSIX-only")
def test_foreground_timeout_returns_promptly_and_reaps():
    """A foreground command that times out reports timeout and returns quickly.

    On timeout the executor must SIGKILL the wrapper's process group and drain
    output under a hard bound, so it cannot hang on an inherited pipe. Group-kill
    of disowned grandchildren is asserted separately in
    ``test_terminate_kills_child_process_group``.
    """

    async def _run():
        start = time.monotonic()
        result = await asyncio.wait_for(
            execute_shell_command("sleep 30", timeout=1),
            timeout=10,
        )
        elapsed = time.monotonic() - start
        assert result.status == "error"
        assert "timed out" in result.output
        assert elapsed < 8, f"foreground timeout path took too long: {elapsed:.1f}s"

    asyncio.run(_run())


def test_new_session_kwargs_posix_vs_windows():
    """_new_session_kwargs sets start_new_session on POSIX, nothing on Windows."""
    kwargs = shell_executor._new_session_kwargs()
    if IS_WINDOWS:
        assert kwargs == {}
    else:
        assert kwargs == {"start_new_session": True}


@pytest.mark.skipif(IS_WINDOWS, reason="process-group semantics are POSIX-only")
def test_signal_process_group_refuses_own_group():
    """A child in OUR process group must never be group-signalled.

    Safety regression: a child spawned WITHOUT start_new_session shares this
    process's group. Calling killpg on it would kill the test runner. The helper
    must detect this and signal only the child instead.
    """

    async def _run():
        # No start_new_session -> child shares our process group.
        process = await asyncio.create_subprocess_shell(
            "sleep 30",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert os.getpgid(process.pid) == os.getpgid(0)
            # Must NOT raise or kill us; must terminate only the child.
            shell_executor._kill_process_group(process)
            await asyncio.wait_for(process.wait(), timeout=5)
            assert process.returncode is not None
            # We (the runner) are obviously still alive to make this assertion.
            assert os.getpgid(0) == os.getpgid(0)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(_run())
