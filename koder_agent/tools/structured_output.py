"""Structured output tool for returning JSON data in headless/SDK mode."""

from __future__ import annotations

import json

from .compat import function_tool


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

    # Try to parse as JSON and return it validated
    try:
        parsed = json.loads(data)
        return json.dumps(parsed)
    except (json.JSONDecodeError, TypeError):
        # If not valid JSON, wrap in a result object
        return json.dumps({"result": data})
