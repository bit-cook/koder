"""Hooks for tool execution with permission checking."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import Agent, RunContextWrapper, RunHooks, Tool

from koder_agent.harness.hooks.runtime import dispatch_command_hooks

if TYPE_CHECKING:
    from koder_agent.harness.permissions.service import PermissionService

logger = logging.getLogger(__name__)


class ApprovalHooks(RunHooks):
    """RunHooks implementation that checks permissions before tool execution."""

    def __init__(
        self,
        wrapped_hooks: RunHooks,
        permission_service: PermissionService | None = None,
    ):
        """Initialize approval hooks.

        Args:
            wrapped_hooks: Optional hooks to wrap (e.g., display hooks)
            permission_service: Optional permission service for tool-level checks.
                When provided, tool calls are evaluated against the active
                permission mode before execution.
        """
        self.wrapped_hooks = wrapped_hooks
        self._permission_service = permission_service

    async def on_agent_start(self, context: RunContextWrapper, agent: Agent) -> None:
        """Called before the agent is invoked."""
        if self.wrapped_hooks:
            await self.wrapped_hooks.on_agent_start(context, agent)

    async def on_agent_end(self, context: RunContextWrapper, agent: Agent, output: Any) -> None:
        """Called when the agent produces a final output."""
        if self.wrapped_hooks:
            await self.wrapped_hooks.on_agent_end(context, agent, output)
        result = dispatch_command_hooks(
            cwd=Path.cwd(),
            event_name="Stop",
            match_value=None,
            payload={
                "event": "Stop",
                "agent_type": getattr(agent, "name", None),
                "last_assistant_message": str(output),
                "stop_hook_active": getattr(self, "_stop_hook_active", False),
            },
        )
        if result.blocked:
            self._stop_hook_active = True
            raise RuntimeError(result.block_reason or "Blocked by Stop hook")

    async def on_handoff(
        self, context: RunContextWrapper, from_agent: Agent, to_agent: Agent
    ) -> None:
        """Called when a handoff occurs."""
        if self.wrapped_hooks and hasattr(self.wrapped_hooks, "on_handoff"):
            await self.wrapped_hooks.on_handoff(context, from_agent, to_agent)

    async def on_tool_start(self, context: RunContextWrapper, agent: Agent, tool: Tool) -> None:
        """Check permissions, then forward to wrapped hooks for tool display.

        Raises:
            PermissionError: When the permission service denies the tool call.
        """
        if self._permission_service is not None:
            # on_tool_start receives the Tool but NOT its arguments,
            # so we can only perform tool-level checks (e.g. "is write_file
            # allowed in plan mode?").  Argument-level checks (specific file
            # paths, shell commands) happen inside each tool function.
            result = self._permission_service.evaluate_tool_call(
                tool_name=tool.name,
                arguments={},
            )
            if not result.allowed and not result.requires_approval:
                logger.warning("Permission denied for tool %s: %s", tool.name, result.reason)
                raise PermissionError(f"Permission denied for {tool.name}: {result.reason}")
            if result.requires_approval:
                # In non-interactive contexts approval cannot be obtained;
                # log and allow so the existing interactive approval flow
                # (if any) still works.
                logger.info("Tool %s requires approval: %s", tool.name, result.reason)

        if self.wrapped_hooks:
            await self.wrapped_hooks.on_tool_start(context, agent, tool)

    async def on_tool_end(
        self, context: RunContextWrapper, agent: Agent, tool: Tool, result: str
    ) -> None:
        """Called after a tool is invoked."""
        if self.wrapped_hooks:
            await self.wrapped_hooks.on_tool_end(context, agent, tool, result)
        dispatch_command_hooks(
            cwd=Path.cwd(),
            event_name="PostToolUse",
            match_value=tool.name,
            payload={
                "event": "PostToolUse",
                "tool_name": tool.name,
                "tool_input": {},
                "result": str(result),
            },
        )
