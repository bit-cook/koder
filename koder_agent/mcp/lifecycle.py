"""Shared MCP server lifecycle helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any


async def cleanup_mcp_servers(
    servers: Iterable[Any],
    *,
    logger: logging.Logger | None = None,
    timeout: float = 3.0,
    propagate_cancellation: bool = True,
) -> None:
    """Best-effort cleanup that finishes before caller cancellation propagates.

    Set ``propagate_cancellation`` to false when the caller is already handling
    the exception it must propagate. Later cancellations are still drained while
    the shielded cleanup finishes, but they do not replace that entering error.
    """
    server_list = list(servers)
    if not server_list:
        return

    cleanup_task = asyncio.create_task(
        _cleanup_mcp_servers(server_list, logger=logger, timeout=timeout),
        name="mcp-server-cleanup",
    )
    cancellation: asyncio.CancelledError | None = None
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as exc:
            if cleanup_task.cancelled():
                raise
            if cancellation is None:
                cancellation = exc

    cleanup_task.result()
    if cancellation is not None and propagate_cancellation:
        raise cancellation


async def _cleanup_mcp_servers(
    servers: list[Any],
    *,
    logger: logging.Logger | None,
    timeout: float,
) -> None:
    """Clean every owned server once inside a cancellation-shielded task."""
    for server in servers:
        cleanup = getattr(server, "cleanup", None)
        if cleanup is None:
            continue
        try:
            await asyncio.wait_for(cleanup(), timeout=timeout)
        except asyncio.TimeoutError:
            if logger is not None:
                logger.debug(
                    "Timed out cleaning up MCP server %s",
                    getattr(server, "name", ""),
                )
        except Exception as exc:
            if logger is not None:
                logger.debug(
                    "Failed to clean up MCP server %s: %s",
                    getattr(server, "name", ""),
                    exc,
                    exc_info=True,
                )
