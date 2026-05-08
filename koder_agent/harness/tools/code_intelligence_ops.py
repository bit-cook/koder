"""Harness wrapper for the local code-intelligence tool."""

from __future__ import annotations

from typing import Any

from koder_agent.tools.code_intelligence import code_intelligence

from .registry import ToolRegistry, ToolSpec, build_tool_result

CODE_INTELLIGENCE_ERROR_MARKERS = (
    "Error:",
    "Unsupported operation:",
)


async def invoke_code_intelligence(arguments: dict[str, Any]) -> dict[str, Any]:
    operation = arguments.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        return build_tool_result(
            "code_intelligence", "Missing required argument: operation", status="error"
        )
    output = code_intelligence(
        operation=operation,
        path=arguments.get("path"),
        query=arguments.get("query"),
        line=arguments.get("line"),
        character=arguments.get("character"),
        limit=arguments.get("limit", 50),
    )
    return build_tool_result(
        "code_intelligence",
        output,
        error_markers=CODE_INTELLIGENCE_ERROR_MARKERS,
    )


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(name="code_intelligence", invoke=invoke_code_intelligence, category="code")
    )
