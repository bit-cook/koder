"""Harness wrappers for search tools."""

from __future__ import annotations

import json
from typing import Any

from koder_agent.tools.search import glob_search, grep_search

from .registry import ToolRegistry, ToolSpec, build_tool_result

SEARCH_ERROR_MARKERS = (
    "Path does not exist",
    "Path is not a directory",
    "Invalid regex pattern",
    "Glob search error",
    "Grep search error",
)


async def _invoke_decorated_tool(name: str, tool, payload: dict[str, Any]) -> dict[str, Any]:
    output = await tool.on_invoke_tool(None, json.dumps(payload))
    return build_tool_result(name, output, error_markers=SEARCH_ERROR_MARKERS)


async def invoke_glob_search(arguments: dict[str, Any]) -> dict[str, Any]:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return build_tool_result(
            "glob_search", "Missing required argument: pattern", status="error"
        )
    payload = {"pattern": pattern, "path": arguments.get("path")}
    return await _invoke_decorated_tool("glob_search", glob_search, payload)


async def invoke_grep_search(arguments: dict[str, Any]) -> dict[str, Any]:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return build_tool_result(
            "grep_search", "Missing required argument: pattern", status="error"
        )
    payload = {
        "pattern": pattern,
        "path": arguments.get("path"),
        "include": arguments.get("include"),
    }
    return await _invoke_decorated_tool("grep_search", grep_search, payload)


def register_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(name="glob_search", invoke=invoke_glob_search, category="search"))
    registry.register(ToolSpec(name="grep_search", invoke=invoke_grep_search, category="search"))
