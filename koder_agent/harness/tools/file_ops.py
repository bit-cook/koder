"""Harness wrappers for file tools."""

from __future__ import annotations

import json
from typing import Any

from koder_agent.tools.file import edit_file, read_file, write_file

from .registry import ToolRegistry, ToolSpec, build_tool_result

FILE_ERROR_MARKERS = (
    "File not found",
    "Error ",
    "Permission denied",
    "Failed ",
)


def _require_argument(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str) and value:
        return value
    return None


async def _invoke_decorated_tool(name: str, tool, payload: dict[str, Any]) -> dict[str, Any]:
    output = await tool.on_invoke_tool(None, json.dumps(payload))
    return build_tool_result(name, output, error_markers=FILE_ERROR_MARKERS)


async def invoke_read_file(arguments: dict[str, Any]) -> dict[str, Any]:
    file_path = _require_argument(arguments, "file_path") or _require_argument(arguments, "path")
    if not file_path:
        return build_tool_result(
            "read_file", "Missing required argument: file_path", status="error"
        )
    payload = {
        "path": file_path,
        "offset": arguments.get("offset"),
        "limit": arguments.get("limit"),
    }
    return await _invoke_decorated_tool("read_file", read_file, payload)


async def invoke_write_file(arguments: dict[str, Any]) -> dict[str, Any]:
    file_path = _require_argument(arguments, "file_path") or _require_argument(arguments, "path")
    content = arguments.get("content")
    if not file_path:
        return build_tool_result(
            "write_file", "Missing required argument: file_path", status="error"
        )
    if not isinstance(content, str):
        return build_tool_result("write_file", "Missing required argument: content", status="error")
    payload = {"path": file_path, "content": content}
    return await _invoke_decorated_tool("write_file", write_file, payload)


async def invoke_edit_file(arguments: dict[str, Any]) -> dict[str, Any]:
    file_path = _require_argument(arguments, "file_path") or _require_argument(arguments, "path")
    if not file_path:
        return build_tool_result(
            "edit_file", "Missing required argument: file_path", status="error"
        )

    # Build payload based on which mode arguments are present
    payload: dict[str, Any] = {"path": file_path}

    old_string = arguments.get("old_string")
    new_string = arguments.get("new_string")
    diff = arguments.get("diff")

    if old_string is not None and new_string is not None:
        # String replacement mode
        payload["old_string"] = old_string
        payload["new_string"] = new_string
        if arguments.get("replace_all"):
            payload["replace_all"] = True
    elif isinstance(diff, str):
        # Diff mode
        payload["diff"] = diff
    else:
        return build_tool_result(
            "edit_file",
            "Either (old_string + new_string) or diff must be provided.",
            status="error",
        )

    return await _invoke_decorated_tool("edit_file", edit_file, payload)


def register_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(name="read_file", invoke=invoke_read_file, category="file"))
    registry.register(ToolSpec(name="write_file", invoke=invoke_write_file, category="file"))
    registry.register(ToolSpec(name="edit_file", invoke=invoke_edit_file, category="file"))
