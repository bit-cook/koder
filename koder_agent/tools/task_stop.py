"""TaskStop tool — stop running background tasks."""

from __future__ import annotations

import json
from typing import Any, Optional

from koder_agent.tools.shell import BackgroundShellManager

from .compat import function_tool

_manager: Any = None


def _set_shell_manager(mgr: Any) -> None:
    global _manager
    _manager = mgr


def _get_manager():
    if _manager is not None:
        return _manager
    return BackgroundShellManager


async def task_stop(
    task_id: Optional[str] = None,
    shell_id: Optional[str] = None,
) -> str:
    """Kill a running background task by ID.

    Args:
        task_id: Task ID to stop.
        shell_id: Deprecated alias for task_id (backward compatibility).
    """
    effective_id = task_id or shell_id
    if not effective_id:
        return json.dumps({"error": "Either task_id or shell_id is required"})

    mgr = _get_manager()
    shell = mgr.get(effective_id)

    if shell is None:
        return json.dumps({"error": f"Task {effective_id} not found"})

    if getattr(shell, "status", "") != "running":
        return json.dumps(
            {
                "error": f"Task {effective_id} is not running (status: {getattr(shell, 'status', 'unknown')})"
            }
        )

    # Await terminate() directly on the running loop. The subprocess and its
    # transport are bound to the loop that spawned them, so terminating here
    # (rather than via asyncio.run in a separate thread/loop) avoids cross-loop
    # hangs that could leave the process alive.
    await mgr.terminate(effective_id)

    return json.dumps(
        {
            "message": f"Successfully stopped task: {effective_id} ({getattr(shell, 'command', '')})",
            "task_id": effective_id,
            "task_type": "local_bash",
            "command": getattr(shell, "command", ""),
        }
    )


task_stop_tool = function_tool(task_stop)
