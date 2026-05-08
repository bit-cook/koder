"""Test that ToolSearch gets populated during agent creation."""

from koder_agent.tools.tool_search import _get_deferred_tools, _set_deferred_tools


def test_deferred_tools_populated_after_set():
    """Verify the deferred tools registry works when populated."""

    class FakeTool:
        def __init__(self, name, description=""):
            self.name = name
            self.description = description

    tools = [FakeTool("read_file", "Read"), FakeTool("write_file", "Write")]
    _set_deferred_tools(tools)
    result = _get_deferred_tools()
    assert len(result) == 2
    assert result[0].name == "read_file"
    _set_deferred_tools(None)


def test_deferred_tools_empty_by_default():
    """Without population, returns empty list."""
    _set_deferred_tools(None)
    result = _get_deferred_tools()
    assert result == []
