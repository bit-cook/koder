"""Tests for ToolSearch deferred tool discovery."""

import json

from koder_agent.tools.tool_search import _set_deferred_tools, tool_search


def _make_tool(name, description=""):
    class FakeTool:
        pass

    t = FakeTool()
    t.name = name
    t.description = description
    return t


def test_keyword_search():
    tools = [
        _make_tool("read_file", "Read a file from disk"),
        _make_tool("write_file", "Write content to a file"),
        _make_tool("web_search", "Search the web"),
    ]
    _set_deferred_tools(tools)
    result = json.loads(tool_search(query="file", max_results=5))
    names = result["matches"]
    assert "read_file" in names
    assert "write_file" in names
    assert "web_search" not in names
    _set_deferred_tools(None)


def test_select_mode():
    tools = [
        _make_tool("read_file", "Read a file"),
        _make_tool("write_file", "Write a file"),
        _make_tool("edit_file", "Edit a file"),
    ]
    _set_deferred_tools(tools)
    result = json.loads(tool_search(query="select:read_file,edit_file", max_results=5))
    assert set(result["matches"]) == {"read_file", "edit_file"}
    _set_deferred_tools(None)


def test_max_results_limit():
    tools = [_make_tool(f"tool_{i}", f"description {i}") for i in range(20)]
    _set_deferred_tools(tools)
    result = json.loads(tool_search(query="tool", max_results=3))
    assert len(result["matches"]) == 3
    _set_deferred_tools(None)


def test_no_matches():
    _set_deferred_tools([_make_tool("read_file", "read")])
    result = json.loads(tool_search(query="nonexistent"))
    assert result["matches"] == []
    _set_deferred_tools(None)


def test_required_term_with_plus():
    tools = [
        _make_tool("mcp__server__list", "List MCP server tools"),
        _make_tool("mcp__server__read", "Read from MCP server"),
        _make_tool("web_search", "Search the web"),
    ]
    _set_deferred_tools(tools)
    result = json.loads(tool_search(query="+mcp list"))
    names = result["matches"]
    assert "mcp__server__list" in names
    assert "web_search" not in names
    _set_deferred_tools(None)
