"""Tests for TaskOutput tool (deprecated but functional)."""

import json

from koder_agent.tools.task_output import _set_shell_lookup, task_output


def _make_shell(shell_id, status="running", exit_code=None, output_lines=None):
    class FakeShell:
        pass

    s = FakeShell()
    s.shell_id = shell_id
    s.status = status
    s.exit_code = exit_code
    s.output_lines = output_lines or []
    s.command = "echo hello"
    return s


def test_task_output_completed():
    shell = _make_shell("t1", status="completed", exit_code=0, output_lines=["hello"])
    _set_shell_lookup(lambda tid: shell)
    result = json.loads(task_output(task_id="t1"))
    assert result["retrieval_status"] == "success"
    assert result["task"]["status"] == "completed"
    assert result["task"]["exit_code"] == 0
    assert "hello" in result["task"]["output"]
    _set_shell_lookup(None)


def test_task_output_running_no_block():
    shell = _make_shell("t2", status="running")
    _set_shell_lookup(lambda tid: shell)
    result = json.loads(task_output(task_id="t2", block=False))
    assert result["retrieval_status"] == "not_ready"
    _set_shell_lookup(None)


def test_task_output_not_found():
    _set_shell_lookup(lambda tid: None)
    result = json.loads(task_output(task_id="missing"))
    assert result["retrieval_status"] == "not_ready"
    assert result["task"] is None
    _set_shell_lookup(None)


def test_task_output_failed():
    shell = _make_shell("t3", status="failed", exit_code=1, output_lines=["error"])
    _set_shell_lookup(lambda tid: shell)
    result = json.loads(task_output(task_id="t3"))
    assert result["retrieval_status"] == "success"
    assert result["task"]["exit_code"] == 1
    _set_shell_lookup(None)
