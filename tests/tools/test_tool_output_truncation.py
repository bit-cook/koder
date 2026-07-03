"""Tests for tool-output truncation in the function_tool wrapper."""

import asyncio
import json

from koder_agent.tools.compat import (
    DEFAULT_MAX_TOOL_OUTPUT_CHARS,
    function_tool,
)


def invoke_tool(tool, args_dict):
    """Helper to invoke a function tool synchronously without a runner context."""
    return asyncio.run(tool.on_invoke_tool(None, json.dumps(args_dict)))


def _make_echo_tool():
    """Build a function tool that echoes back a string of a requested size."""

    @function_tool
    def echo(size: int, fill: str = "x") -> str:
        """Return a string of ``size`` characters made of ``fill``."""
        return fill * size

    return echo


def _make_dict_tool():
    """Build a function tool that returns a non-string (dict) result."""

    @function_tool
    def make_dict(size: int) -> dict:
        """Return a dict whose value is a large string."""
        return {"value": "y" * size}

    return make_dict


def test_small_output_passes_through_unchanged():
    """Output within the threshold is returned verbatim."""
    tool = _make_echo_tool()
    result = invoke_tool(tool, {"size": 100})
    assert result == "x" * 100
    assert "[truncated" not in result


def test_large_output_is_truncated_with_marker():
    """Oversized string output is truncated to a head+tail window with a marker."""
    tool = _make_echo_tool()
    size = DEFAULT_MAX_TOOL_OUTPUT_CHARS * 3
    result = invoke_tool(tool, {"size": size})

    assert "...[truncated" in result
    assert "characters]..." in result
    # Truncated output is much smaller than the original payload.
    assert len(result) < size
    # The marker reports the exact number of removed characters.
    removed = size - (len(result) - len("\n...[truncated  characters]...\n") - len(str(size)))
    assert removed > 0


def test_truncation_keeps_head_and_tail():
    """Head and tail content are preserved around the marker."""
    tool = _make_echo_tool()
    size = DEFAULT_MAX_TOOL_OUTPUT_CHARS * 2
    result = invoke_tool(tool, {"size": size, "fill": "a"})
    head, _, tail = result.partition("\n...[truncated")
    assert head.startswith("a")
    assert tail.rstrip().endswith("a")


def test_env_override_lowers_threshold(monkeypatch):
    """KODER_MAX_TOOL_OUTPUT_CHARS overrides the default threshold."""
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "50")
    tool = _make_echo_tool()

    # 200 chars now exceeds the lowered threshold and gets truncated.
    result = invoke_tool(tool, {"size": 200})
    assert "...[truncated" in result
    assert len(result) < 200


def test_env_override_can_keep_small_output(monkeypatch):
    """A high override threshold lets larger output pass through."""
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", str(DEFAULT_MAX_TOOL_OUTPUT_CHARS * 10))
    tool = _make_echo_tool()
    size = DEFAULT_MAX_TOOL_OUTPUT_CHARS * 2
    result = invoke_tool(tool, {"size": size})
    assert "[truncated" not in result
    assert len(result) == size


def test_invalid_env_falls_back_to_default(monkeypatch):
    """Non-numeric / non-positive env values fall back to the default."""
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "not-a-number")
    tool = _make_echo_tool()
    result = invoke_tool(tool, {"size": 100})
    assert result == "x" * 100


def test_non_string_result_is_not_truncated():
    """Dict (non-string) results are left untouched regardless of size."""
    tool = _make_dict_tool()
    size = DEFAULT_MAX_TOOL_OUTPUT_CHARS * 3
    result = invoke_tool(tool, {"size": size})
    # Non-string results are returned as-is (a dict here), never truncated.
    assert isinstance(result, dict)
    assert result["value"] == "y" * size
