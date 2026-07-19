"""TaskOutput tool — read background task output (deprecated)."""

from __future__ import annotations

import json
from typing import Any, Callable

from koder_agent.tools.shell import BackgroundShellManager

from .compat import (
    _get_max_tool_output_chars,
    _truncate_text_output,
    function_tool,
    tool_output_too_large_json,
)

_shell_lookup: Callable[[str], Any] | None = None


def _set_shell_lookup(fn: Callable[[str], Any] | None) -> None:
    global _shell_lookup
    _shell_lookup = fn


def _get_shell(task_id: str):
    if _shell_lookup is not None:
        return _shell_lookup(task_id)
    return BackgroundShellManager.get(task_id)


def _serialize_task_payload(payload: dict[str, Any]) -> str:
    """Serialize TaskOutput's owned schema, truncating only ``task.output``."""
    max_chars = _get_max_tool_output_chars()
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return serialized

    task = payload.get("task")
    output = task.get("output") if isinstance(task, dict) else None
    if not isinstance(output, str):
        return tool_output_too_large_json(max_chars, original_chars=len(serialized))

    bounded_payload = dict(payload)
    bounded_task = dict(task)
    bounded_payload["task"] = bounded_task
    bounded_task.update(
        output="",
        output_truncated=True,
        output_original_chars=len(output),
    )
    empty_output = json.dumps(bounded_payload, ensure_ascii=False, separators=(",", ":"))
    available = max_chars - len(empty_output)
    if available < 0:
        return tool_output_too_large_json(max_chars, original_chars=len(serialized))

    bounded_task["output"] = _truncate_text_output(output, available)
    candidate = json.dumps(bounded_payload, ensure_ascii=False, separators=(",", ":"))
    if len(candidate) <= max_chars:
        return candidate

    # Escaping can expand a raw string. A one-sixth fallback covers JSON's
    # worst-case ``\uXXXX`` expansion without iterative whole-payload searches.
    bounded_task["output"] = _truncate_text_output(output, available // 6)
    candidate = json.dumps(bounded_payload, ensure_ascii=False, separators=(",", ":"))
    if len(candidate) <= max_chars:
        return candidate
    return tool_output_too_large_json(max_chars, original_chars=len(serialized))


def task_output(task_id: str, block: bool = True, timeout: int = 30000) -> str:
    """Retrieve output from a background task. (Deprecated — prefer Read on output file path.)

    Args:
        task_id: Task/shell ID to retrieve output from.
        block: If true, wait for completion up to timeout. If false, return immediately.
        timeout: Max wait time in milliseconds (default 30000, range 0-600000).
    """
    shell = _get_shell(task_id)

    if shell is None:
        return _serialize_task_payload({"retrieval_status": "not_ready", "task": None})

    is_done = shell.status in ("completed", "failed", "terminated", "error")

    if not is_done and not block:
        return _serialize_task_payload(
            {
                "retrieval_status": "not_ready",
                "task": {
                    "task_id": task_id,
                    "task_type": "local_bash",
                    "status": shell.status,
                    "description": getattr(shell, "command", ""),
                    "output": "",
                },
            }
        )

    if not is_done:
        return _serialize_task_payload(
            {
                "retrieval_status": "timeout",
                "task": {
                    "task_id": task_id,
                    "task_type": "local_bash",
                    "status": shell.status,
                    "description": getattr(shell, "command", ""),
                    "output": "\n".join(getattr(shell, "output_lines", [])),
                },
            }
        )

    output = "\n".join(getattr(shell, "output_lines", []))
    return _serialize_task_payload(
        {
            "retrieval_status": "success",
            "task": {
                "task_id": task_id,
                "task_type": "local_bash",
                "status": shell.status,
                "description": getattr(shell, "command", ""),
                "output": output,
                "exit_code": getattr(shell, "exit_code", None),
            },
        }
    )


task_output_tool = function_tool(task_output)
