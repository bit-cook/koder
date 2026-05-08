"""MCP server mode -- expose koder tools as an MCP server over stdio."""

from __future__ import annotations

import json
import logging
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

logger = logging.getLogger(__name__)

# Tools that should be excluded when serving koder as an MCP server.
# These are agent-internal primitives that don't make sense for external callers.
_EXCLUDED_TOOLS = frozenset(
    {
        "task_delegate",
        "send_message",
        "team_create",
        "team_delete",
        "agent_tool",
    }
)


def _get_koder_version() -> str:
    """Return the installed koder package version."""
    try:
        from importlib.metadata import version

        return version("koder")
    except Exception:
        return "0.0.0"


def _build_tool_list() -> tuple[list[types.Tool], dict[str, Any]]:
    """Build the MCP tool list and a name-to-tool mapping from koder tools.

    Returns a tuple of (mcp_tools, koder_tool_map) where *koder_tool_map*
    maps tool name to the original koder ``FunctionTool`` object so we can
    dispatch ``tools/call`` requests.
    """
    from koder_agent.tools import get_all_tools

    koder_tools = get_all_tools()
    mcp_tools: list[types.Tool] = []
    tool_map: dict[str, Any] = {}

    for tool in koder_tools:
        name: str = getattr(tool, "name", "")
        if not name or name in _EXCLUDED_TOOLS:
            continue

        description: str = getattr(tool, "description", name)
        schema: dict[str, Any] = {}
        if hasattr(tool, "params_json_schema"):
            schema = tool.params_json_schema

        mcp_tools.append(
            types.Tool(
                name=name,
                description=description,
                inputSchema=schema or {"type": "object", "properties": {}},
            )
        )
        tool_map[name] = tool

    return mcp_tools, tool_map


def create_mcp_server() -> Server:
    """Create and configure the MCP ``Server`` instance."""
    server = Server("koder", version=_get_koder_version())
    mcp_tools, tool_map = _build_tool_list()

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return mcp_tools

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[types.TextContent]:
        koder_tool = tool_map.get(name)
        if koder_tool is None:
            raise ValueError(f"Unknown tool: {name}")

        try:
            result = await koder_tool.on_invoke_tool(None, json.dumps(arguments or {}))
            return [types.TextContent(type="text", text=str(result))]
        except Exception as exc:
            logger.exception("Tool %s raised an error", name)
            raise ValueError(f"Tool error: {exc}") from exc

    return server


async def start_mcp_server() -> None:
    """Start koder as an MCP server over stdio transport.

    This function blocks until the client disconnects (stdin closes).
    """
    server = create_mcp_server()
    init_options = server.create_initialization_options()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)
