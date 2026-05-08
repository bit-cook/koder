"""MCP server reconnection with exponential backoff."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

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
