"""Tests for TaskStop tool."""

import json

from koder_agent.tools.task_stop import _set_shell_manager, task_stop


class FakeShellManager:
    def __init__(self):
        self.shells = {}
        self.terminated = []

    def get(self, shell_id):
        return self.shells.get(shell_id)

    async def terminate(self, shell_id):
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
    result = json.loads(task_stop(task_id="s1"))
    assert result["task_id"] == "s1"
    assert "message" in result
    assert "s1" in mgr.terminated
    _set_shell_manager(None)


def test_task_stop_not_found():
    mgr = FakeShellManager()
    _set_shell_manager(mgr)
    result = json.loads(task_stop(task_id="missing"))
    assert "error" in result
    _set_shell_manager(None)


def test_task_stop_not_running():
    mgr = FakeShellManager()
    mgr.shells["s2"] = FakeShell("s2", status="completed")
    _set_shell_manager(mgr)
    result = json.loads(task_stop(task_id="s2"))
    assert "error" in result
    _set_shell_manager(None)


def test_task_stop_shell_id_alias():
    mgr = FakeShellManager()
    mgr.shells["s3"] = FakeShell("s3", status="running")
    _set_shell_manager(mgr)
    result = json.loads(task_stop(shell_id="s3"))
    assert result["task_id"] == "s3"
    _set_shell_manager(None)
