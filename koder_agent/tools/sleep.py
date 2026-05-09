"""Sleep tool for agent self-throttling."""

from __future__ import annotations

import asyncio

from .compat import function_tool


@function_tool
async def sleep_tool(seconds: int = 5) -> str:
    """Pause execution for the specified number of seconds.

    Useful when waiting for external processes to complete or when
    rate-limited. Maximum sleep duration is 300 seconds (5 minutes).

    Args:
        seconds: Number of seconds to sleep (1-300, default 5)
    """
    seconds = max(1, min(seconds, 300))
    await asyncio.sleep(seconds)
    return f"Slept for {seconds} seconds."
