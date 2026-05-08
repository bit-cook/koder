"""Guardrail for enforcing plan mode tool restrictions.

When plan mode is active, only read-only and planning tools are permitted.
Write operations (edit_file, write_file, run_shell, etc.) are rejected
with an informative message directing the user to exit plan mode first.

This follows the same pattern as skill_guardrail.py.
"""

from agents import (
    ToolGuardrailFunctionOutput,
    ToolInputGuardrail,
    ToolInputGuardrailData,
)

from ..tools.plan_mode import _get_plan_service


def plan_mode_tool_restriction_guardrail(
    data: ToolInputGuardrailData,
) -> ToolGuardrailFunctionOutput:
    """Guardrail that enforces plan mode tool restrictions.

    When plan mode is active, only tools listed in
    ``_PLAN_MODE_ALLOWED_TOOLS`` can be used.  All other tools are
    rejected with a message explaining that write operations are
    restricted in plan mode.

    Args:
        data: The guardrail input data containing tool context.

    Returns:
        ToolGuardrailFunctionOutput indicating whether to allow or reject.
    """
    svc = _get_plan_service()

    # Not in plan mode - allow everything
    if not svc.is_plan_mode():
        return ToolGuardrailFunctionOutput.allow()

    tool_name = getattr(data.context, "tool_name", "") or ""
    if not tool_name:
        return ToolGuardrailFunctionOutput.allow()

    allowed = svc.get_allowed_tools_in_plan()
    if tool_name in allowed:
        return ToolGuardrailFunctionOutput.allow()

    # Tool is not allowed in plan mode - reject
    message = (
        f"Tool '{tool_name}' is not permitted in plan mode. "
        f"Write operations are restricted while planning. "
        f"Allowed tools: {', '.join(sorted(allowed))}. "
        f"Use exit_plan_mode to return to normal execution mode."
    )

    return ToolGuardrailFunctionOutput.reject_content(
        message=message,
        output_info={
            "blocked_tool": tool_name,
            "reason": "plan_mode_restriction",
            "allowed_tools": sorted(allowed),
        },
    )


# Create the guardrail instance for use in agent configuration
plan_mode_restriction_guardrail = ToolInputGuardrail(
    guardrail_function=plan_mode_tool_restriction_guardrail,
    name="plan_mode_tool_restrictions",
)
