"""Tests for approval hooks with permission checking."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from agents import Tool

from koder_agent.agentic.approval_hooks import ApprovalHooks
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
