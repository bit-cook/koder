"""TaskStop tool — stop running background tasks."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from agents import function_tool

from koder_agent.tools.shell import BackgroundShellManager

_manager: Any = None


def _set_shell_manager(mgr: Any) -> None:
    global _manager
    _manager = mgr


def _get_manager():
    if _manager is not None:
        return _manager
    return BackgroundShellManager


def task_stop(
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

    # Terminate — handle async manager.terminate()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, mgr.terminate(effective_id)).result()
    else:
        asyncio.run(mgr.terminate(effective_id))

    return json.dumps(
        {
            "message": f"Successfully stopped task: {effective_id} ({getattr(shell, 'command', '')})",
            "task_id": effective_id,
            "task_type": "local_bash",
            "command": getattr(shell, "command", ""),
        }
    )


task_stop_tool = function_tool(task_stop)
