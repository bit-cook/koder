"""Tests for tool-output truncation in the function_tool wrapper."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from agents.items import ItemHelpers
from agents.tool import ToolOutputFileContent, ToolOutputImage, ToolOutputText

from koder_agent.tools.compat import (
    DEFAULT_MAX_TOOL_OUTPUT_CHARS,
    MIN_TOOL_OUTPUT_ERROR_CHARS,
    StructuredOutputTooLargeError,
    function_tool,
    parse_json_with_limits,
)
from koder_agent.tools.permission_context import (
    reset_tool_permission_context,
    set_tool_permission_context,
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


def _make_value_tool(value):
    """Build a function tool that returns a native value."""

    @function_tool
    def make_value() -> Any:
        """Return the captured value."""
        return value

    return make_value


def _make_json_tool(value):
    """Build a function tool that returns ``value`` serialized as JSON."""

    @function_tool
    def make_json() -> str:
        """Return a JSON string."""
        return json.dumps(value)

    return make_json


def _make_raw_tool(value: str):
    @function_tool
    def make_raw() -> str:
        """Return the captured string unchanged."""
        return value

    return make_raw


def test_small_output_passes_through_unchanged():
    """Output within the threshold is returned verbatim."""
    tool = _make_echo_tool()
    result = invoke_tool(tool, {"size": 100})
    assert result == "x" * 100


def test_direct_function_tool_invocation_rejects_undeclared_fields():
    tool = _make_echo_tool()

    result = invoke_tool(tool, {"size": 10, "fill": "x", "path": "/tmp/ignored"})

    assert tool.params_json_schema["additionalProperties"] is False
    assert "undeclared argument" in result
    assert "path" in result
    assert "[truncated" not in result


def test_undeclared_argument_error_obeys_tool_output_cap(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "80")
    tool = _make_echo_tool()
    undeclared_key = "x" * 5000

    result = invoke_tool(tool, {"size": 10, undeclared_key: "ignored"})

    assert len(result) <= 80
    assert result.startswith("Invalid JSON input")
    assert "...[truncated" in result
    assert result.endswith("x")


def test_permission_denial_obeys_tool_output_cap(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "80")
    invoked = False

    @function_tool
    def run_shell(command: str) -> str:
        """Run a test command."""
        nonlocal invoked
        invoked = True
        return command

    service = MagicMock()
    service.evaluate_tool_call_async = AsyncMock(
        return_value=SimpleNamespace(
            allowed=False,
            requires_approval=False,
            reason="r" * 5000,
        )
    )
    token = set_tool_permission_context(service)
    try:
        result = invoke_tool(run_shell, {"command": "deploy"})
    finally:
        reset_tool_permission_context(token)

    assert invoked is False
    assert len(result) <= 80
    assert result.startswith("Permission denied")
    assert "...[truncated" in result
    assert result.endswith("r")


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
    assert len(result) <= 50


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


@pytest.mark.parametrize(
    "value",
    [
        ToolOutputText(text="hello"),
        ToolOutputImage(image_url="https://example.test/image.png"),
        ToolOutputFileContent(file_id="file_123"),
        {"type": "text", "text": "dict variant"},
        [
            ToolOutputText(text="first"),
            {"type": "image", "file_id": "file_image"},
        ],
    ],
)
def test_sdk_native_outputs_preserve_original_type_and_identity(value):
    """The SDK must receive native structured outputs, not JSON strings."""
    result = invoke_tool(_make_value_tool(value), {})

    assert result is value
    assert isinstance(ItemHelpers._convert_tool_output(result), list)


def test_other_non_string_outputs_are_not_globally_serialized(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "50")
    value = {"value": "y" * 5000}

    result = invoke_tool(_make_value_tool(value), {})

    assert result is value


def test_oversized_json_object_is_replaced_not_mutated(monkeypatch):
    """Generic JSON schemas are never recursively edited to make them fit."""
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "500")
    tool = _make_json_tool(
        {
            "status": "success",
            "payload": {"output": "BEGIN" + "x" * 5000 + "END"},
        }
    )

    result = invoke_tool(tool, {})
    parsed = json.loads(result)

    assert len(result) <= 500
    assert parsed["error"] == "tool_output_too_large"
    assert "status" not in parsed
    assert parsed["original_chars"] > 500


def test_oversized_json_list_returns_the_same_error_contract(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "400")
    tool = _make_json_tool(["x" * 5000, {"tail": "kept"}])

    result = invoke_tool(tool, {})
    parsed = json.loads(result)

    assert len(result) <= 400
    assert parsed["error"] == "tool_output_too_large"
    assert "value" not in parsed


def test_existing_reserved_keys_are_not_overwritten(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "300")
    original = {
        "error": "domain_error",
        "_koder_truncation": {"domain": True},
        "payload": "x" * 5000,
    }
    result = invoke_tool(_make_json_tool(original), {})

    assert original["error"] == "domain_error"
    assert original["_koder_truncation"] == {"domain": True}
    assert json.loads(result)["error"] == "tool_output_too_large"


def test_huge_number_json_string_fails_closed_before_text_handling(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "500")
    raw_json = '{"value":' + "9" * 5000 + "}"

    result = invoke_tool(_make_raw_tool(raw_json), {})

    assert json.loads(result)["error"] == "tool_output_too_large"


def test_valid_json_string_is_not_reserialized_or_key_collapsed():
    raw_json = '{"key":1,"key":2}'

    result = invoke_tool(_make_raw_tool(raw_json), {})

    assert result == raw_json


def test_json_validator_preserves_duplicate_object_pairs():
    parsed = parse_json_with_limits('{"key":1,"key":2}')

    assert [key for key, _value in parsed] == ["key", "key"]


def test_json_validator_rejects_extreme_input_before_parser_work(monkeypatch):
    raw_json = " " * 1_000_001 + "null"

    with monkeypatch.context() as scoped:
        scoped.setattr(
            "koder_agent.tools.compat.json.loads",
            lambda *_args, **_kwargs: pytest.fail("json.loads should not run"),
        )
        with pytest.raises(StructuredOutputTooLargeError):
            parse_json_with_limits(raw_json)


def test_ordinary_text_above_json_work_limit_keeps_head_and_tail(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "120")
    plain_text = "BEGIN ordinary log\n" + "x" * 1_000_100 + "\nEND ordinary log"

    result = invoke_tool(_make_raw_tool(plain_text), {})

    assert len(result) <= 120
    assert result.startswith("BEGIN ordinary log")
    assert "...[truncated" in result
    assert result.endswith("END ordinary log")
    assert "tool_output_too_large" not in result


def test_valid_json_above_parser_work_limit_keeps_structured_error(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "120")
    raw_json = '{"payload":"' + "x" * 1_000_100 + '"}'

    result = invoke_tool(_make_raw_tool(raw_json), {})

    assert json.loads(result)["error"] == "tool_output_too_large"


def test_tiny_json_cap_uses_protocol_minimum(monkeypatch):
    """Tiny caps still return an explicit error, never a false scalar value."""
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "1")
    tool = _make_json_tool({"payload": "x" * 100})

    result = invoke_tool(tool, {})

    assert len(result) <= MIN_TOOL_OUTPUT_ERROR_CHARS
    assert json.loads(result) == {"error": "tool_output_too_large"}


def test_oversized_numeric_identifier_is_deterministically_treated_as_json(monkeypatch):
    """A syntactically valid numeric identifier follows the JSON-scalar rule."""
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "100")
    tool = _make_echo_tool()

    result = invoke_tool(tool, {"size": 1000, "fill": "7"})

    assert json.loads(result)["error"] == "tool_output_too_large"


@pytest.mark.parametrize("scalar", ["true", "null", '"value"'])
def test_oversized_json_scalars_with_leading_whitespace_return_error(monkeypatch, scalar):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "100")
    raw_json = " " * 200 + scalar

    result = invoke_tool(_make_raw_tool(raw_json), {})

    assert json.loads(result)["error"] == "tool_output_too_large"


@pytest.mark.parametrize("prefix", ["[INFO] ", "{text ", '"unterminated '])
def test_oversized_json_like_plain_text_uses_head_tail_truncation(monkeypatch, prefix):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "100")
    plain_text = prefix + "x" * 1000

    result = invoke_tool(_make_raw_tool(plain_text), {})

    assert "...[truncated" in result
    assert len(result) <= 100


def test_oversized_duplicate_key_json_is_valid_without_key_collapse(monkeypatch):
    monkeypatch.setenv("KODER_MAX_TOOL_OUTPUT_CHARS", "100")
    raw_json = '{"key":1,"key":2,"payload":"' + "x" * 500 + '"}'

    result = invoke_tool(_make_raw_tool(raw_json), {})

    assert json.loads(result)["error"] == "tool_output_too_large"
