"""Compatibility helpers for SDK-decorated tools."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from agents import FunctionTool
from agents import function_tool as _agents_function_tool
from agents.tool_context import ToolContext


def _wrap_none_context(tool: FunctionTool) -> FunctionTool:
    """Allow direct test/harness invocation of SDK tools without a runner context."""

    original_on_invoke_tool = tool.on_invoke_tool

    @wraps(original_on_invoke_tool)
    async def _on_invoke_tool(ctx: ToolContext[Any] | None, input_json: str) -> Any:
        if ctx is None:
            ctx = ToolContext(
                context=None,
                tool_name=tool.name,
                tool_call_id=f"manual-{tool.name}",
                tool_arguments=input_json,
            )
        return await original_on_invoke_tool(ctx, input_json)

    tool.on_invoke_tool = _on_invoke_tool
    return tool


def function_tool(func: Callable[..., Any] | None = None, **kwargs: Any) -> Any:
    """Wrap ``agents.function_tool`` while preserving legacy ``ctx=None`` invocations."""

    decorated = _agents_function_tool(func, **kwargs)
    if isinstance(decorated, FunctionTool):
        return _wrap_none_context(decorated)

    def _decorator(real_func: Callable[..., Any]) -> FunctionTool:
        return _wrap_none_context(decorated(real_func))

    return _decorator
