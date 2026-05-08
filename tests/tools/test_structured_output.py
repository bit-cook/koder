"""Tests for structured output tool."""

import asyncio
import json

from koder_agent.tools.structured_output import structured_output


def invoke_tool(tool, args_dict):
    """Helper to invoke a function tool synchronously."""
    return asyncio.run(tool.on_invoke_tool(None, json.dumps(args_dict)))


def test_tool_exists():
    """Tool should be importable."""
    assert structured_output is not None


def test_returns_json_string():
    """Tool should return the JSON data as a string."""
    data = json.dumps({"answer": 42, "explanation": "The meaning of life"})
    result = invoke_tool(structured_output, {"data": data})
    parsed = json.loads(result)
    assert parsed["answer"] == 42


def test_accepts_dict_data():
    """Tool should accept dict data and return it serialized."""
    result = invoke_tool(structured_output, {"data": json.dumps({"key": "value"})})
    parsed = json.loads(result)
    assert parsed["key"] == "value"


def test_accepts_list_data():
    """Tool should accept list data."""
    result = invoke_tool(structured_output, {"data": json.dumps([1, 2, 3])})
    parsed = json.loads(result)
    assert parsed == [1, 2, 3]


def test_invalid_json_returns_as_string():
    """Non-JSON data should be returned wrapped in a result object."""
    result = invoke_tool(structured_output, {"data": "just plain text"})
    assert "just plain text" in result


def test_empty_data():
    """Empty data should return empty result."""
    result = invoke_tool(structured_output, {"data": ""})
    assert result is not None


def test_nested_json():
    """Should handle deeply nested JSON."""
    nested = {"level1": {"level2": {"level3": [1, 2, {"key": "deep"}]}}}
    result = invoke_tool(structured_output, {"data": json.dumps(nested)})
    parsed = json.loads(result)
    assert parsed["level1"]["level2"]["level3"][2]["key"] == "deep"
