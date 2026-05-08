"""TaskOutput tool — read background task output (deprecated)."""

from __future__ import annotations

import json
from typing import Any, Callable

from agents import function_tool

from koder_agent.tools.shell import BackgroundShellManager

_shell_lookup: Callable[[str], Any] | None = None


def _set_shell_lookup(fn: Callable[[str], Any] | None) -> None:
    global _shell_lookup
    _shell_lookup = fn


def _get_shell(task_id: str):
    if _shell_lookup is not None:
        return _shell_lookup(task_id)
    return BackgroundShellManager.get(task_id)


def task_output(task_id: str, block: bool = True, timeout: int = 30000) -> str:
    """Retrieve output from a background task. (Deprecated — prefer Read on output file path.)

    Args:
        task_id: Task/shell ID to retrieve output from.
        block: If true, wait for completion up to timeout. If false, return immediately.
        timeout: Max wait time in milliseconds (default 30000, range 0-600000).
    """
    shell = _get_shell(task_id)

    if shell is None:
        return json.dumps({"retrieval_status": "not_ready", "task": None})

    is_done = shell.status in ("completed", "failed", "terminated", "error")

    if not is_done and not block:
        return json.dumps(
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
        return json.dumps(
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
    return json.dumps(
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
