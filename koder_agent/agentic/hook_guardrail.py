"""Tool guardrail for project and skill-scoped PreToolUse hooks."""

from __future__ import annotations

import json
from pathlib import Path

from agents import (
    ToolGuardrailFunctionOutput,
    ToolInputGuardrail,
    ToolInputGuardrailData,
)

from koder_agent.harness.hooks.runtime import dispatch_command_hooks_async


async def hook_pretool_guardrail(
    data: ToolInputGuardrailData,
) -> ToolGuardrailFunctionOutput:
    tool_name = getattr(data.context, "tool_name", "") or ""
    if not tool_name:
        return ToolGuardrailFunctionOutput.allow()

    raw_args = getattr(data.context, "tool_arguments", None)
    parsed_args = {}
    if isinstance(raw_args, str):
        try:
            loaded = json.loads(raw_args)
            if isinstance(loaded, dict):
                parsed_args = loaded
        except Exception:
            parsed_args = {}
    elif isinstance(raw_args, dict):
        parsed_args = raw_args

    # Run the blocking hook I/O off the event loop so a slow PreToolUse hook
    # cannot freeze streaming, subagents, or cron. The SDK's
    # ToolInputGuardrail.run awaits awaitable guardrail results.
    result = await dispatch_command_hooks_async(
        cwd=Path.cwd(),
        event_name="PreToolUse",
        match_value=tool_name,
        payload={
            "event": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": parsed_args,
        },
    )
    if result.blocked:
        return ToolGuardrailFunctionOutput.reject_content(
            message=result.block_reason or "Blocked by PreToolUse hook",
            output_info={
                "blocked_tool": tool_name,
                "hook_event": "PreToolUse",
            },
        )
    return ToolGuardrailFunctionOutput.allow()


hook_pretool_input_guardrail = ToolInputGuardrail(
    guardrail_function=hook_pretool_guardrail,
    name="hook_pretool_guardrail",
)
