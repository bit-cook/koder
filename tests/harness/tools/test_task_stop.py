"""Tests for TaskStop tool."""

import asyncio
import json

import pytest

from koder_agent.tools.task_stop import _set_shell_manager, task_stop


class FakeShellManager:
    def __init__(self):
        self.shells = {}
        self.terminated = []
        # Records the event loop that was running when terminate() was awaited.
        self.terminate_loops = []

    def get(self, shell_id):
        return self.shells.get(shell_id)

    async def terminate(self, shell_id):
        self.terminate_loops.append(asyncio.get_running_loop())
        shell = self.shells.pop(shell_id, None)
        if shell is None:
            raise ValueError(f"Shell not found: {shell_id}")
        self.terminated.append(shell_id)
        return shell


class FakeShell:
    def __init__(self, shell_id, status="running", command="echo test"):
        self.shell_id = shell_id
        self.status = status
        self.command = command


def test_task_stop_running():
    mgr = FakeShellManager()
    mgr.shells["s1"] = FakeShell("s1", status="running", command="sleep 100")
    _set_shell_manager(mgr)
    result = json.loads(asyncio.run(task_stop(task_id="s1")))
    assert result["task_id"] == "s1"
    assert "message" in result
    assert "s1" in mgr.terminated
    _set_shell_manager(None)


def test_task_stop_not_found():
    mgr = FakeShellManager()
    _set_shell_manager(mgr)
    result = json.loads(asyncio.run(task_stop(task_id="missing")))
    assert "error" in result
    _set_shell_manager(None)


def test_task_stop_not_running():
    mgr = FakeShellManager()
    mgr.shells["s2"] = FakeShell("s2", status="completed")
    _set_shell_manager(mgr)
    result = json.loads(asyncio.run(task_stop(task_id="s2")))
    assert "error" in result
    _set_shell_manager(None)


def test_task_stop_shell_id_alias():
    mgr = FakeShellManager()
    mgr.shells["s3"] = FakeShell("s3", status="running")
    _set_shell_manager(mgr)
    result = json.loads(asyncio.run(task_stop(shell_id="s3")))
    assert result["task_id"] == "s3"
    _set_shell_manager(None)


def test_task_stop_terminates_on_the_running_loop():
    """terminate() must be awaited on the same loop that runs task_stop.

    Regression test for the old ThreadPoolExecutor + asyncio.run() path that
    ran terminate() on a *different* event loop than the one owning the
    subprocess transport, which could hang and leave the process alive.
    """
    mgr = FakeShellManager()
    mgr.shells["s4"] = FakeShell("s4", status="running")
    _set_shell_manager(mgr)

    async def _run():
        current = asyncio.get_running_loop()
        result = json.loads(await task_stop(task_id="s4"))
        return current, result

    running_loop, result = asyncio.run(_run())
    assert result["task_id"] == "s4"
    # terminate() saw exactly one loop, and it is the loop that awaited task_stop.
    assert mgr.terminate_loops == [running_loop]
    _set_shell_manager(None)


@pytest.mark.asyncio
async def test_task_stop_terminates_real_background_shell():
    """An async task_stop terminates a live background shell process."""
    from koder_agent.tools.shell import (
        IS_WINDOWS,
        BackgroundShell,
        BackgroundShellManager,
    )

    if IS_WINDOWS:
        command = "Start-Sleep -Seconds 30"
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-Command",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    else:
        command = "sleep 30"
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

    shell_id = "bg-real"
    bg_shell = BackgroundShell(
        shell_id=shell_id,
        command=command,
        process=process,
        start_time=0.0,
    )
    BackgroundShellManager.add(bg_shell)

    # Use the real manager (no fake override) so terminate() runs on this loop.
    _set_shell_manager(None)
    try:
        result = json.loads(await task_stop(task_id=shell_id))
        assert result["task_id"] == shell_id
        assert shell_id not in BackgroundShellManager.get_available_ids()
        # The process must actually be dead.
        assert process.returncode is not None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if shell_id in BackgroundShellManager.get_available_ids():
            await BackgroundShellManager.terminate(shell_id)
