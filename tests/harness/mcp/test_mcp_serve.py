"""Tests for koder MCP server mode (``koder mcp serve``)."""

from __future__ import annotations

import asyncio
from argparse import Namespace
from unittest.mock import AsyncMock, patch

from koder_agent.mcp.serve import _build_tool_list, create_mcp_server

# ---------------------------------------------------------------------------
# Tool list construction
# ---------------------------------------------------------------------------


class TestBuildToolList:
    """Verify that ``_build_tool_list`` correctly translates koder tools."""

    def test_returns_tools_and_map(self):
        mcp_tools, tool_map = _build_tool_list()
        assert len(mcp_tools) > 0
        assert len(tool_map) > 0
        # Every MCP tool must also appear in the mapping
        for t in mcp_tools:
            assert t.name in tool_map

    def test_excluded_tools_absent(self):
        """Agent-internal tools must not be exposed via MCP."""
        mcp_tools, tool_map = _build_tool_list()
        exposed_names = {t.name for t in mcp_tools}
        for excluded in (
            "task_delegate",
            "send_message",
            "team_create",
            "team_delete",
            "agent_tool",
            "todo_read",
            "todo_write",
        ):
            assert excluded not in exposed_names
            assert excluded not in tool_map

    def test_core_tools_present(self):
        """Key user-facing tools must be exposed."""
        mcp_tools, _ = _build_tool_list()
        exposed_names = {t.name for t in mcp_tools}
        for expected in (
            "read_file",
            "write_file",
            "edit_file",
            "glob_search",
            "grep_search",
            "run_shell",
        ):
            assert expected in exposed_names

    def test_tool_has_name_and_schema(self):
        """Every tool must have a name and a valid inputSchema dict."""
        mcp_tools, _ = _build_tool_list()
        for t in mcp_tools:
            assert t.name
            assert isinstance(t.inputSchema, dict)


# ---------------------------------------------------------------------------
# Server creation
# ---------------------------------------------------------------------------


class TestCreateMcpServer:
    """Verify that ``create_mcp_server`` produces a valid MCP Server."""

    def test_server_has_handlers(self):
        server = create_mcp_server()
        from mcp import types

        assert types.ListToolsRequest in server.request_handlers
        assert types.CallToolRequest in server.request_handlers


# ---------------------------------------------------------------------------
# CLI parser acceptance
# ---------------------------------------------------------------------------


class TestCliParserAcceptsServe:
    def test_mcp_serve_parses(self):
        from koder_agent.cli import _build_cli_parser

        parser = _build_cli_parser("mcp")
        args = parser.parse_args(["mcp", "serve"])
        assert args.command == "mcp"
        assert args.mcp_action == "serve"


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


class TestHandleMcpSubcommandServe:
    def test_serve_action_dispatches(self):
        """``handle_mcp_subcommand`` must call ``start_mcp_server`` for serve."""
        mock_start = AsyncMock()
        args = Namespace(mcp_action="serve")

        with patch(
            "koder_agent.mcp.serve.start_mcp_server",
            mock_start,
        ):
            from koder_agent.harness.mcp.commands import handle_mcp_subcommand

            result = asyncio.run(handle_mcp_subcommand(args))

        assert result == 0
        mock_start.assert_awaited_once()


# ---------------------------------------------------------------------------
# End-to-end tool invocation through the server handlers
# ---------------------------------------------------------------------------


class TestToolCallViaServer:
    """Exercise the ``call_tool`` handler registered on the MCP server."""

    def test_call_known_tool(self):
        """Calling a known tool (list_directory) should succeed."""

        async def _run():
            server = create_mcp_server()
            from mcp import types

            handler = server.request_handlers[types.CallToolRequest]

            request = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="list_directory",
                    arguments={"path": "."},
                ),
            )
            response = await handler(request)
            result = response.root
            assert hasattr(result, "content")
            assert len(result.content) > 0
            assert result.content[0].type == "text"

        asyncio.run(_run())

    def test_call_unknown_tool_errors(self):
        """Calling an unknown tool should return an error result."""

        async def _run():
            server = create_mcp_server()
            from mcp import types

            handler = server.request_handlers[types.CallToolRequest]

            request = types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="nonexistent_tool_xyz",
                    arguments={},
                ),
            )
            response = await handler(request)
            result = response.root
            assert result.isError is True

        asyncio.run(_run())

    def test_todo_tools_are_not_callable_without_an_mcp_request_identity(self):
        async def _run():
            server = create_mcp_server()
            from mcp import types

            handler = server.request_handlers[types.CallToolRequest]
            requests = [
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(name=name, arguments={}),
                )
                for name in ("todo_read", "todo_write")
            ]
            responses = await asyncio.gather(*(handler(request) for request in requests))
            assert all(response.root.isError is True for response in responses)

        asyncio.run(_run())

    def test_list_tools_handler(self):
        """The list_tools handler should return all exposed tools."""

        async def _run():
            server = create_mcp_server()
            from mcp import types

            handler = server.request_handlers[types.ListToolsRequest]

            request = types.ListToolsRequest(
                method="tools/list",
                params=None,
            )
            response = await handler(request)
            result = response.root
            assert hasattr(result, "tools")
            names = {t.name for t in result.tools}
            assert "read_file" in names
            assert "task_delegate" not in names

        asyncio.run(_run())
