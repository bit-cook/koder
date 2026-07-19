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


def test_oversized_structured_output_fails_explicitly(monkeypatch):
    """Oversized structured output never masquerades as the requested schema."""
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "500")
    data = json.dumps({"answer": 42, "explanation": "x" * 5000})

    result = invoke_tool(structured_output, {"data": data})
    parsed = json.loads(result)

    assert len(result) <= 500
    assert parsed["error"] == "tool_output_too_large"
    assert parsed["original_chars"] == len(data)
    assert parsed["max_chars"] == 500
    assert "answer" not in parsed


def test_deep_json_returns_too_large_error(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "10000")
    data = "[" * 100 + "0" + "]" * 100

    result = invoke_tool(structured_output, {"data": data})

    assert json.loads(result)["error"] == "tool_output_too_large"


def test_huge_json_integer_within_output_limit_is_preserved(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "10000")
    data = '{"value":' + "9" * 5000 + "}"

    result = invoke_tool(structured_output, {"data": data})

    assert result == data


def test_wide_json_object_returns_too_large_error(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "500000")
    data = json.dumps({f"key_{index}": index for index in range(10001)})

    result = invoke_tool(structured_output, {"data": data})

    assert json.loads(result)["error"] == "tool_output_too_large"


def test_duplicate_object_keys_are_rejected_explicitly():
    data = '{"role":"user","role":"admin"}'

    result = invoke_tool(structured_output, {"data": data})

    assert json.loads(result) == {"error": "duplicate_json_key", "key": "role"}
