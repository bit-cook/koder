"""Structured output tool for returning JSON data in headless/SDK mode."""

from __future__ import annotations

import json

from .compat import (
    DuplicateJSONKeyError,
    InvalidJSONConstantError,
    StructuredOutputTooLargeError,
    _get_max_tool_output_chars,
    function_tool,
    parse_json_with_limits,
    serialize_bounded_json,
    tool_output_too_large_json,
)


@function_tool
def structured_output(data: str) -> str:
    """Return structured JSON output to the caller.

    Use this tool when you need to return structured data (JSON) as the
    final response. This is primarily used in headless/SDK mode when the
    caller expects machine-readable output.

    Args:
        data: JSON string containing the structured output data.

    Returns:
        The validated JSON data as a string.
    """
    if not data:
        return json.dumps({"result": None})

    max_chars = _get_max_tool_output_chars()
    if len(data) > max_chars:
        return tool_output_too_large_json(max_chars, original_chars=len(data))

    # Try to parse as JSON and return it validated
    try:
        parse_json_with_limits(data, reject_duplicate_keys=True)
        return data
    except DuplicateJSONKeyError as exc:
        return serialize_bounded_json(
            {"error": "duplicate_json_key", "key": exc.key},
            max_chars,
        )
    except StructuredOutputTooLargeError:
        return tool_output_too_large_json(max_chars, original_chars=len(data))
    except (InvalidJSONConstantError, json.JSONDecodeError, TypeError):
        # If not valid JSON, wrap in a result object
        return serialize_bounded_json({"result": data}, max_chars)
