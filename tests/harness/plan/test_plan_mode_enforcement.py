"""Tests that plan mode actually restricts tool calls."""

import pytest

from koder_agent.agentic.plan_guardrail import plan_mode_tool_restriction_guardrail
from koder_agent.harness.plan.mode import PlanModeService
from koder_agent.tools.plan_mode import _set_plan_service

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_allowed(result) -> bool:
    """Return True if the guardrail output indicates 'allow'."""
    return result.behavior.get("type") == "allow"


def _is_rejected(result) -> bool:
    """Return True if the guardrail output indicates 'reject_content'."""
    return result.behavior.get("type") == "reject_content"


class _FakeToolContext:
    """Minimal stand-in for the tool-call context object."""

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        self.tool_arguments = {}


class _FakeGuardrailData:
    """Wraps _FakeToolContext to look like ToolInputGuardrailData."""

    def __init__(self, tool_name: str):
        self.context = _FakeToolContext(tool_name)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _plan_service_cleanup():
    """Ensure the plan service singleton is cleaned up after each test."""
    yield
    _set_plan_service(None)


# ---------------------------------------------------------------------------
# Allowed-tools list tests
# ---------------------------------------------------------------------------


def test_plan_mode_blocks_write_tools():
    """When plan mode is active, write tools should be rejected by guardrail."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    # Verify the allowed tools list excludes write tools
    allowed = svc.get_allowed_tools_in_plan()
    assert "write_file" not in allowed
    assert "edit_file" not in allowed
    assert "run_shell" not in allowed
    assert "append_file" not in allowed

    # Verify read tools are allowed
    assert "read_file" in allowed
    assert "glob_search" in allowed
    assert "grep_search" in allowed


def test_plan_mode_allows_read_tools():
    """Read-only tools should work in plan mode."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    allowed = svc.get_allowed_tools_in_plan()
    assert "read_file" in allowed
    assert "glob_search" in allowed
    assert "grep_search" in allowed
    assert "web_search" in allowed
    assert "task_create" in allowed
    assert "tool_search" in allowed


# ---------------------------------------------------------------------------
# Guardrail function tests
# ---------------------------------------------------------------------------


def test_guardrail_allows_when_not_in_plan_mode():
    """When not in plan mode, the guardrail should allow any tool."""
    svc = PlanModeService()
    _set_plan_service(svc)

    data = _FakeGuardrailData("write_file")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_allowed(result)


def test_guardrail_blocks_write_tool_in_plan_mode():
    """When plan mode is active, write_file should be blocked."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    data = _FakeGuardrailData("write_file")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_rejected(result)
    assert result.output_info["blocked_tool"] == "write_file"
    assert result.output_info["reason"] == "plan_mode_restriction"


def test_guardrail_blocks_edit_file_in_plan_mode():
    """edit_file should be blocked in plan mode."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    data = _FakeGuardrailData("edit_file")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_rejected(result)
    assert result.output_info["blocked_tool"] == "edit_file"


def test_guardrail_blocks_run_shell_in_plan_mode():
    """run_shell should be blocked in plan mode."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    data = _FakeGuardrailData("run_shell")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_rejected(result)
    assert result.output_info["blocked_tool"] == "run_shell"


def test_guardrail_allows_read_file_in_plan_mode():
    """read_file should be allowed in plan mode."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    data = _FakeGuardrailData("read_file")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_allowed(result)


def test_guardrail_allows_grep_search_in_plan_mode():
    """grep_search should be allowed in plan mode."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    data = _FakeGuardrailData("grep_search")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_allowed(result)


def test_guardrail_allows_exit_plan_mode_in_plan_mode():
    """exit_plan_mode should always be allowed so the user can leave plan mode."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    data = _FakeGuardrailData("exit_plan_mode")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_allowed(result)


def test_guardrail_unblocks_after_exit_plan_mode():
    """After exiting plan mode, write tools should work again."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    # Confirm blocked
    data = _FakeGuardrailData("write_file")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_rejected(result)

    # Exit plan mode
    svc.exit_plan_mode()

    # Now should be allowed
    data = _FakeGuardrailData("write_file")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_allowed(result)


def test_guardrail_blocks_append_file_in_plan_mode():
    """append_file should be blocked in plan mode."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    data = _FakeGuardrailData("append_file")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_rejected(result)
    assert result.output_info["blocked_tool"] == "append_file"


def test_guardrail_blocks_git_command_in_plan_mode():
    """git_command should be blocked in plan mode."""
    svc = PlanModeService()
    _set_plan_service(svc)
    svc.enter_plan_mode()

    data = _FakeGuardrailData("git_command")
    result = plan_mode_tool_restriction_guardrail(data)
    assert _is_rejected(result)
    assert result.output_info["blocked_tool"] == "git_command"
