"""Harness wrappers for web tools."""

from __future__ import annotations

import json
from typing import Any

from koder_agent.tools.web import web_fetch, web_search

from .registry import ToolRegistry, ToolSpec, build_tool_result

WEB_ERROR_MARKERS = (
    "Invalid query",
    "Search failed",
    "Search error",
    "Invalid URL format",
    "Only HTTP/HTTPS URLs are supported",
    "Failed to fetch URL",
    "Request timed out",
    "Request failed",
    "Error fetching content",
)


async def _invoke_decorated_tool(name: str, tool, payload: dict[str, Any]) -> dict[str, Any]:
    output = await tool.on_invoke_tool(None, json.dumps(payload))
    return build_tool_result(name, output, error_markers=WEB_ERROR_MARKERS)


async def invoke_web_search(arguments: dict[str, Any]) -> dict[str, Any]:
    query = arguments.get("query")
    if not isinstance(query, str):
        return build_tool_result("web_search", "Missing required argument: query", status="error")
    payload = {
        "query": query,
        "max_results": arguments.get("max_results", 3),
    }
    return await _invoke_decorated_tool("web_search", web_search, payload)


async def invoke_web_fetch(arguments: dict[str, Any]) -> dict[str, Any]:
    url = arguments.get("url")
    prompt = arguments.get("prompt")
    if not isinstance(url, str) or not url:
        return build_tool_result("web_fetch", "Missing required argument: url", status="error")
    if not isinstance(prompt, str):
        return build_tool_result("web_fetch", "Missing required argument: prompt", status="error")
    payload = {"url": url, "prompt": prompt}
    return await _invoke_decorated_tool("web_fetch", web_fetch, payload)


def register_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(name="web_fetch", invoke=invoke_web_fetch, category="web"))
    registry.register(ToolSpec(name="web_search", invoke=invoke_web_search, category="web"))
