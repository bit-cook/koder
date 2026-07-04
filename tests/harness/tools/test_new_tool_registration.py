"""Tests that all 13 new orchestration tools are registered correctly."""

import platform

import pytest


def test_new_tools_registered():
    """All new tools appear in get_all_tools by default."""
    from koder_agent.tools import get_all_tools

    tools = get_all_tools()
    names = {getattr(t, "name", None) for t in tools}

    # Task lifecycle
    assert "task_create" in names
    assert "task_update" in names
    assert "task_get" in names
    assert "task_list" in names

    # Plan mode
    assert "enter_plan_mode" in names
    assert "exit_plan_mode" in names

    # Worktree
    assert "enter_worktree" in names
    assert "exit_worktree" in names

    # ToolSearch
    assert "tool_search" in names

    # Config
    assert "config_tool" in names

    # Cron
    assert "cron_create" in names
    assert "cron_delete" in names
    assert "cron_list" in names

    # PowerShell is Windows-only
    if platform.system() == "Windows":
        assert "run_powershell" in names
    else:
        assert "run_powershell" not in names
    assert "code_intelligence" in names


def test_all_new_tools_are_function_tools():
    """All new tools are FunctionTool instances with guardrails attached."""
    from agents import FunctionTool

    from koder_agent.tools import get_all_tools

    tools = get_all_tools()
    new_tool_names = {
        "task_create",
        "task_update",
        "task_get",
        "task_list",
        "enter_plan_mode",
        "exit_plan_mode",
        "enter_worktree",
        "exit_worktree",
        "tool_search",
        "config_tool",
        "cron_create",
        "cron_delete",
        "cron_list",
    }

    for tool in tools:
        name = getattr(tool, "name", None)
        if name in new_tool_names:
            assert isinstance(tool, FunctionTool), f"{name} is not a FunctionTool"
            assert tool.tool_input_guardrails is not None, f"{name} has no guardrails"
            assert len(tool.tool_input_guardrails) >= 2, f"{name} should have at least 2 guardrails"


@pytest.mark.skipif(platform.system() != "Windows", reason="run_powershell is Windows-only")
def test_powershell_tool_is_registered_with_guardrails():
    from agents import FunctionTool

    from koder_agent.tools import get_all_tools

    tools = get_all_tools()
    tool = next(item for item in tools if getattr(item, "name", None) == "run_powershell")

    assert isinstance(tool, FunctionTool)
    assert tool.tool_input_guardrails is not None
    assert len(tool.tool_input_guardrails) >= 2


def test_powershell_tool_not_registered_on_non_windows():
    if platform.system() == "Windows":
        pytest.skip("run_powershell is expected on Windows")

    from koder_agent.tools import get_all_tools

    names = {getattr(t, "name", None) for t in get_all_tools()}
    assert "run_powershell" not in names


def test_code_intelligence_tool_is_registered_with_guardrails():
    from agents import FunctionTool

    from koder_agent.tools import get_all_tools

    tools = get_all_tools()
    tool = next(item for item in tools if getattr(item, "name", None) == "code_intelligence")

    assert isinstance(tool, FunctionTool)
    assert tool.tool_input_guardrails is not None
    assert len(tool.tool_input_guardrails) >= 2


def test_repl_primitive_surface_is_directly_registered_with_guardrails():
    from agents import FunctionTool

    from koder_agent.tools import get_all_tools

    tools = get_all_tools()
    by_name = {getattr(tool, "name", None): tool for tool in tools}

    primitive_names = {
        "read_file",
        "write_file",
        "edit_file",
        "glob_search",
        "grep_search",
        "run_shell",
        "notebook_edit",
        "agent_tool",
    }

    assert primitive_names <= set(by_name)
    assert "REPL" not in by_name
    assert "repl" not in by_name

    for name in primitive_names:
        tool = by_name[name]
        assert isinstance(tool, FunctionTool)
        assert tool.tool_input_guardrails is not None
        assert len(tool.tool_input_guardrails) >= 2
