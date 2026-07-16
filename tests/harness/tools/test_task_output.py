"""Tests for TaskOutput tool (deprecated but functional)."""

import asyncio
import json

from koder_agent.tools.task_output import _set_shell_lookup, task_output, task_output_tool


def invoke_tool(tool, args_dict):
    """Invoke an SDK function tool without a runner context."""
    return asyncio.run(tool.on_invoke_tool(None, json.dumps(args_dict)))


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


def test_large_task_output_remains_valid_json(monkeypatch):
    """TaskOutput truncates only its documented output leaf."""
    shell = _make_shell(
        "large",
        status="completed",
        exit_code=0,
        output_lines=["BEGIN" + "x" * 5000 + "END"],
    )
    _set_shell_lookup(lambda tid: shell)
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "600")

    result = invoke_tool(task_output_tool, {"task_id": "large"})
    parsed = json.loads(result)

    assert len(result) <= 600
    assert parsed["retrieval_status"] == "success"
    assert parsed["task"]["status"] == "completed"
    assert parsed["task"]["output"].startswith("BEGIN")
    assert parsed["task"]["output"].endswith("END")
    assert "truncated" in parsed["task"]["output"]
    assert parsed["task"]["output_truncated"] is True
    assert parsed["task"]["output_original_chars"] == 5008
    assert "_koder_truncation" not in parsed
    _set_shell_lookup(None)
