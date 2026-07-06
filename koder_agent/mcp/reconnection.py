"""MCP server reconnection with exponential backoff."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Upstream defaults
INITIAL_DELAY = 1.0  # seconds
MAX_DELAY = 30.0  # seconds
MAX_ATTEMPTS = 5


@dataclass
class ReconnectionConfig:
    """Configuration for reconnection behavior."""

    initial_delay: float = INITIAL_DELAY
    max_delay: float = MAX_DELAY
    max_attempts: int = MAX_ATTEMPTS


class ReconnectionManager:
    """Manages reconnection attempts with exponential backoff.

    Usage:
        mgr = ReconnectionManager(ReconnectionConfig())
        success = await mgr.reconnect_with_backoff(connect_function)
    """

    def __init__(self, config: ReconnectionConfig | None = None):
        self.config = config or ReconnectionConfig()
        self.attempt_count = 0
        # Retained after a successful initial connect so a later health check
        # can rebuild the connection without re-deriving trust/channel wiring.
        self.config_ref: Any = None
        self.server: Any = None
        self._connect_fn: Callable[[], Awaitable] | None = None

    def bind(
        self,
        *,
        config: Any = None,
        server: Any = None,
        connect_fn: Callable[[], Awaitable] | None = None,
    ) -> None:
        """Retain the server, its config, and the factory closure.

        This lets callers hold onto the manager and later trigger a reconnect
        for a dropped server. Without this the manager was discarded and no
        runtime reconnection was possible.
        """
        if config is not None:
            self.config_ref = config
        if server is not None:
            self.server = server
        if connect_fn is not None:
            self._connect_fn = connect_fn

    def _server_is_healthy(self) -> bool:
        """Best-effort liveness check for the retained server.

        A connected SDK ``MCPServer`` exposes a non-None ``session``; we treat a
        missing/None session as "needs reconnect". Servers that do not expose a
        ``session`` attribute are assumed healthy (nothing to probe).
        """
        server = self.server
        if server is None:
            return False
        if not hasattr(server, "session"):
            return True
        return getattr(server, "session", None) is not None

    async def reconnect_if_needed(self) -> bool:
        """Reconnect the retained server if it looks unhealthy.

        Returns True if the server is healthy (already, or after a successful
        reconnect), False if it is unhealthy and reconnection failed or no
        factory closure was bound.
        """
        if self._server_is_healthy():
            return True
        if self._connect_fn is None:
            logger.debug("reconnect_if_needed: no connect_fn bound; cannot reconnect")
            return False

        connect_fn = self._connect_fn

        async def _do_connect() -> None:
            self.server = await connect_fn()

        return await self.reconnect_with_backoff(_do_connect)

    def get_next_delay(self) -> float:
        """Calculate next delay with exponential backoff."""
        delay = self.config.initial_delay * (2**self.attempt_count)
        self.attempt_count += 1
        return min(delay, self.config.max_delay)

    def should_retry(self) -> bool:
        """Check if more attempts are available."""
        return self.attempt_count < self.config.max_attempts

    def reset(self) -> None:
        """Reset attempt counter after successful connection."""
        self.attempt_count = 0

    async def reconnect_with_backoff(
        self,
        connect_fn: Callable[[], Awaitable],
    ) -> bool:
        """Attempt reconnection with exponential backoff.

        Args:
            connect_fn: Async function that attempts to establish connection.
                       Should raise on failure, return on success.

        Returns:
            True if reconnection succeeded, False if all attempts exhausted.
        """
        self.reset()

        while self.should_retry():
            delay = self.get_next_delay()
            try:
                await connect_fn()
                logger.info(
                    "MCP server reconnected successfully after %d attempts", self.attempt_count
                )
                return True
            except Exception as e:
                logger.warning(
                    "MCP reconnection attempt %d/%d failed: %s (retry in %.1fs)",
                    self.attempt_count,
                    self.config.max_attempts,
                    e,
                    delay,
                )
                if self.should_retry():
                    await asyncio.sleep(delay)

        logger.error(
            "MCP server reconnection failed after %d attempts",
            self.config.max_attempts,
        )
        return False
