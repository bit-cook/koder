"""Tests for approval hooks with permission checking."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agents import Tool

from koder_agent.agentic.approval_hooks import ApprovalHooks, ToolPermissionError
from koder_agent.harness.permissions.modes import PermissionMode
from koder_agent.harness.permissions.service import PermissionService


def _make_wrapped_hooks():
    """Create a mock wrapped_hooks with async methods."""
    wrapped = MagicMock()
    wrapped.on_tool_start = AsyncMock()
    wrapped.on_tool_end = AsyncMock()
    wrapped.on_agent_start = AsyncMock()
    wrapped.on_agent_end = AsyncMock()
    return wrapped


@pytest.fixture
def mock_tool():
    tool = MagicMock(spec=Tool)
    tool.name = "write_file"
    return tool


@pytest.fixture
def permission_service(tmp_path):
    return PermissionService.default(
        mode=PermissionMode.DEFAULT,
        workspace_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_on_tool_start_without_permission_service():
    """Without permission_service, should pass through (backward compat)."""
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks())
    tool = MagicMock(spec=Tool)
    tool.name = "read_file"
    # Should not raise
    await hooks.on_tool_start(MagicMock(), MagicMock(), tool)


@pytest.mark.asyncio
async def test_on_tool_start_allows_read(permission_service):
    """Read operations should be allowed without approval."""
    hooks = ApprovalHooks(
        wrapped_hooks=_make_wrapped_hooks(), permission_service=permission_service
    )
    tool = MagicMock(spec=Tool)
    tool.name = "read_file"
    # Should not raise
    await hooks.on_tool_start(MagicMock(), MagicMock(), tool)


@pytest.mark.asyncio
async def test_on_tool_start_blocks_write_in_plan_mode(tmp_path):
    """Plan mode should block write operations."""
    svc = PermissionService.default(mode=PermissionMode.PLAN, workspace_root=tmp_path)
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks(), permission_service=svc)
    tool = MagicMock(spec=Tool)
    tool.name = "write_file"

    with pytest.raises(PermissionError):
        await hooks.on_tool_start(MagicMock(), MagicMock(), tool)


@pytest.mark.asyncio
async def test_on_tool_start_blocks_shell_in_plan_mode(tmp_path):
    """Plan mode should block shell commands."""
    svc = PermissionService.default(mode=PermissionMode.PLAN, workspace_root=tmp_path)
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks(), permission_service=svc)
    tool = MagicMock(spec=Tool)
    tool.name = "run_shell"

    with pytest.raises(PermissionError):
        await hooks.on_tool_start(MagicMock(), MagicMock(), tool)


@pytest.mark.asyncio
async def test_on_tool_start_blocks_edit_in_plan_mode(tmp_path):
    """Plan mode should block edit operations."""
    svc = PermissionService.default(mode=PermissionMode.PLAN, workspace_root=tmp_path)
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks(), permission_service=svc)
    tool = MagicMock(spec=Tool)
    tool.name = "edit_file"

    with pytest.raises(PermissionError):
        await hooks.on_tool_start(MagicMock(), MagicMock(), tool)


@pytest.mark.asyncio
async def test_on_tool_start_allows_read_in_plan_mode(tmp_path):
    """Plan mode should allow read operations."""
    svc = PermissionService.default(mode=PermissionMode.PLAN, workspace_root=tmp_path)
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks(), permission_service=svc)
    tool = MagicMock(spec=Tool)
    tool.name = "read_file"

    # Should not raise
    await hooks.on_tool_start(MagicMock(), MagicMock(), tool)


@pytest.mark.asyncio
async def test_on_tool_start_allows_in_bypass_mode(tmp_path):
    """Bypass mode should allow all operations."""
    svc = PermissionService.default(mode=PermissionMode.BYPASS, workspace_root=tmp_path)
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks(), permission_service=svc)
    tool = MagicMock(spec=Tool)
    tool.name = "write_file"

    # Should not raise
    await hooks.on_tool_start(MagicMock(), MagicMock(), tool)


@pytest.mark.asyncio
async def test_on_tool_start_forwards_to_wrapped_hooks(permission_service):
    """Should still forward to wrapped hooks after permission check passes."""
    wrapped = _make_wrapped_hooks()
    hooks = ApprovalHooks(wrapped_hooks=wrapped, permission_service=permission_service)
    tool = MagicMock(spec=Tool)
    tool.name = "read_file"
    ctx = MagicMock()
    agent = MagicMock()

    await hooks.on_tool_start(ctx, agent, tool)

    wrapped.on_tool_start.assert_called_once_with(ctx, agent, tool)


@pytest.mark.asyncio
async def test_on_tool_start_does_not_forward_on_deny(tmp_path):
    """Should NOT forward to wrapped hooks when permission is denied."""
    svc = PermissionService.default(mode=PermissionMode.PLAN, workspace_root=tmp_path)
    wrapped = _make_wrapped_hooks()
    hooks = ApprovalHooks(wrapped_hooks=wrapped, permission_service=svc)
    tool = MagicMock(spec=Tool)
    tool.name = "write_file"

    with pytest.raises(PermissionError):
        await hooks.on_tool_start(MagicMock(), MagicMock(), tool)

    wrapped.on_tool_start.assert_not_called()


@pytest.mark.asyncio
async def test_permission_denied_raises_classified_exception(tmp_path):
    """Denied tools raise ToolPermissionError (a classified PermissionError).

    NOTE on the SDK constraint: openai-agents' ``RunHooks.on_tool_start``
    returns None and offers NO way to substitute a tool result from inside the
    hook -- the runner awaits the hook BEFORE invoking the tool and does not
    wrap the hook in the try/except that turns failures into a tool message.
    So raising is the only SDK-supported way to stop a denied tool from the
    hook. We assert it is a *classified* exception carrying tool_name/reason so
    callers can present it cleanly, and that it subclasses PermissionError for
    backward compatibility.
    """
    svc = PermissionService.default(mode=PermissionMode.PLAN, workspace_root=tmp_path)
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks(), permission_service=svc)
    tool = MagicMock(spec=Tool)
    tool.name = "write_file"

    with pytest.raises(ToolPermissionError) as exc_info:
        await hooks.on_tool_start(MagicMock(), MagicMock(), tool)

    assert isinstance(exc_info.value, PermissionError)
    assert exc_info.value.tool_name == "write_file"


@pytest.mark.asyncio
async def test_stop_hook_active_initialized_false():
    """_stop_hook_active must be initialized in __init__ (not lazily)."""
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks())
    assert hooks._stop_hook_active is False


@pytest.mark.asyncio
async def test_on_agent_start_resets_stop_hook_active():
    """A prior Stop-hook block must not persist: on_agent_start resets the flag.

    Without the reset, once a Stop hook trips _stop_hook_active it stays True
    for the life of the scheduler, permanently wedging subsequent runs.
    """
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks())

    # Simulate a previous run having been blocked by a Stop hook.
    hooks._stop_hook_active = True

    await hooks.on_agent_start(MagicMock(), MagicMock())

    assert hooks._stop_hook_active is False


@pytest.mark.asyncio
async def test_on_tool_start_does_not_preemptively_block_shell_without_arguments(
    permission_service,
):
    """Shell permission checks should wait for the actual command payload."""
    wrapped = _make_wrapped_hooks()
    hooks = ApprovalHooks(wrapped_hooks=wrapped, permission_service=permission_service)
    tool = MagicMock(spec=Tool)
    tool.name = "run_shell"
    ctx = MagicMock()
    agent = MagicMock()

    await hooks.on_tool_start(ctx, agent, tool)

    wrapped.on_tool_start.assert_called_once_with(ctx, agent, tool)


@pytest.mark.asyncio
async def test_post_tool_use_dispatch_receives_tool_input_from_context():
    """PostToolUse hooks must receive the actual tool_input, not an empty dict.

    The SDK's ToolContext carries tool_arguments as a raw JSON string.
    on_tool_end parses it and passes it as tool_input in the dispatch payload.
    """
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks())
    tool = MagicMock(spec=Tool)
    tool.name = "write_file"

    # Simulate a ToolContext with tool_arguments
    ctx = MagicMock()
    ctx.tool_arguments = json.dumps({"file_path": "/tmp/test.py", "content": "hello"})
    agent = MagicMock()

    with patch("koder_agent.agentic.approval_hooks.dispatch_command_hooks") as mock_dispatch:
        mock_dispatch.return_value = MagicMock(blocked=False)
        await hooks.on_tool_end(ctx, agent, tool, "ok")

    mock_dispatch.assert_called_once()
    payload = mock_dispatch.call_args[1]["payload"]
    assert payload["tool_input"] == {"file_path": "/tmp/test.py", "content": "hello"}
    assert payload["tool_name"] == "write_file"
    assert payload["result"] == "ok"


@pytest.mark.asyncio
async def test_post_tool_use_dispatch_empty_when_no_tool_arguments():
    """PostToolUse gracefully falls back to empty dict when context lacks tool_arguments."""
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks())
    tool = MagicMock(spec=Tool)
    tool.name = "read_file"

    # Context without tool_arguments attribute (plain RunContextWrapper)
    ctx = MagicMock(spec=[])  # spec=[] means no attributes
    agent = MagicMock()

    with patch("koder_agent.agentic.approval_hooks.dispatch_command_hooks") as mock_dispatch:
        mock_dispatch.return_value = MagicMock(blocked=False)
        await hooks.on_tool_end(ctx, agent, tool, "content")

    payload = mock_dispatch.call_args[1]["payload"]
    assert payload["tool_input"] == {}


@pytest.mark.asyncio
async def test_post_tool_use_dispatch_handles_malformed_json():
    """PostToolUse gracefully falls back to empty dict on malformed JSON."""
    hooks = ApprovalHooks(wrapped_hooks=_make_wrapped_hooks())
    tool = MagicMock(spec=Tool)
    tool.name = "run_shell"

    ctx = MagicMock()
    ctx.tool_arguments = "not valid json {"
    agent = MagicMock()

    with patch("koder_agent.agentic.approval_hooks.dispatch_command_hooks") as mock_dispatch:
        mock_dispatch.return_value = MagicMock(blocked=False)
        await hooks.on_tool_end(ctx, agent, tool, "error")

    payload = mock_dispatch.call_args[1]["payload"]
    assert payload["tool_input"] == {}
