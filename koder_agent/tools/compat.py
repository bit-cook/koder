"""Compatibility helpers for SDK-decorated tools."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from functools import wraps
from typing import Any

from agents import FunctionTool
from agents import function_tool as _agents_function_tool
from agents.tool import default_tool_error_function
from agents.tool_context import ToolContext

from .permission_context import enforce_tool_permission

logger = logging.getLogger(__name__)

# Default maximum number of characters for a tool's string result before it is
# truncated to a head+tail window. Configurable via ``KODER_MAX_TOOL_OUTPUT_CHARS``.
DEFAULT_MAX_TOOL_OUTPUT_CHARS = 30000


def _get_max_tool_output_chars() -> int:
    """Resolve the tool-output truncation threshold from the environment.

    Falls back to :data:`DEFAULT_MAX_TOOL_OUTPUT_CHARS` when the env var is unset,
    empty, non-numeric, or non-positive (the latter disables truncation).
    """

    raw = os.environ.get("KODER_MAX_TOOL_OUTPUT_CHARS")
    if raw is None or raw.strip() == "":
        return DEFAULT_MAX_TOOL_OUTPUT_CHARS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOOL_OUTPUT_CHARS
    if value <= 0:
        return DEFAULT_MAX_TOOL_OUTPUT_CHARS
    return value


def _truncate_tool_output(result: str, max_chars: int) -> str:
    """Truncate ``result`` to a head+tail window with a clear middle marker.

    Keeps roughly the first 70% and last 30% of the budget, joined by a marker
    that reports how many characters were removed. Small outputs (``len`` within
    ``max_chars``) are returned unchanged.
    """

    total = len(result)
    if total <= max_chars:
        return result

    head_len = (max_chars * 7) // 10
    tail_len = max_chars - head_len
    head = result[:head_len]
    tail = result[total - tail_len :] if tail_len > 0 else ""
    removed = total - head_len - tail_len
    marker = f"\n...[truncated {removed} characters]...\n"
    return f"{head}{marker}{tail}"


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
        # Argument-level permission enforcement for the main agent chain. Returns a
        # denial string (fed back to the model) when the active permission service
        # blocks this specific call; None when allowed or when no service is active.
        blocked = await enforce_tool_permission(tool.name, input_json)
        if blocked is not None:
            return blocked
        result = await original_on_invoke_tool(ctx, input_json)
        # Size guard: only truncate oversized *string* results; leave
        # non-string results (dicts/objects) untouched.
        if isinstance(result, str):
            result = _truncate_tool_output(result, _get_max_tool_output_chars())
        return result

    tool.on_invoke_tool = _on_invoke_tool
    return tool


def _dispatching_tool_error_function(ctx: Any, error: Exception) -> str:
    """SDK ``failure_error_function`` that dispatches ``PostToolUseFailure`` hooks.

    The SDK converts tool exceptions into a model-facing error string via this
    callback; it is the one place on the main agent chain that observes every
    tool failure, so the hook event is dispatched here. Hook problems never
    mask the original tool error.
    """
    tool_name = getattr(ctx, "tool_name", None) or "unknown"
    tool_input: dict[str, Any] = {}
    raw_args = getattr(ctx, "tool_arguments", None)
    if raw_args:
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, dict):
                tool_input = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    try:
        from pathlib import Path

        from koder_agent.harness.hooks.runtime import dispatch_command_hooks

        dispatch_command_hooks(
            cwd=Path.cwd(),
            event_name="PostToolUseFailure",
            match_value=tool_name,
            payload={
                "event": "PostToolUseFailure",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "error": str(error),
            },
        )
    except Exception:
        logger.debug("PostToolUseFailure hook dispatch failed for %s", tool_name, exc_info=True)
    return default_tool_error_function(ctx, error)


def function_tool(func: Callable[..., Any] | None = None, **kwargs: Any) -> Any:
    """Wrap ``agents.function_tool`` while preserving legacy ``ctx=None`` invocations."""

    kwargs.setdefault("failure_error_function", _dispatching_tool_error_function)
    decorated = _agents_function_tool(func, **kwargs)
    if isinstance(decorated, FunctionTool):
        return _wrap_none_context(decorated)

    def _decorator(real_func: Callable[..., Any]) -> FunctionTool:
        return _wrap_none_context(decorated(real_func))

    return _decorator
