"""Tests for MCP server reconnection with exponential backoff."""

from unittest.mock import AsyncMock

import pytest

from koder_agent.mcp.reconnection import (
    INITIAL_DELAY,
    MAX_ATTEMPTS,
    MAX_DELAY,
    ReconnectionConfig,
    ReconnectionManager,
)


def test_config_defaults():
    assert INITIAL_DELAY == 1.0
    assert MAX_DELAY == 30.0
    assert MAX_ATTEMPTS == 5


def test_config_custom():
    config = ReconnectionConfig(initial_delay=2.0, max_delay=60.0, max_attempts=3)
    assert config.initial_delay == 2.0
    assert config.max_delay == 60.0
    assert config.max_attempts == 3


def test_exponential_backoff_delays():
    mgr = ReconnectionManager(ReconnectionConfig())
    delays = [mgr.get_next_delay() for _ in range(5)]
    # Should be exponential: 1, 2, 4, 8, 16 (capped at 30)
    assert delays[0] == 1.0
    assert delays[1] == 2.0
    assert delays[2] == 4.0
    assert delays[3] == 8.0
    assert delays[4] == 16.0


def test_backoff_caps_at_max():
    mgr = ReconnectionManager(ReconnectionConfig(initial_delay=1.0, max_delay=5.0))
    delays = [mgr.get_next_delay() for _ in range(10)]
    assert all(d <= 5.0 for d in delays)


def test_reset_clears_attempts():
    mgr = ReconnectionManager(ReconnectionConfig())
    mgr.get_next_delay()
    mgr.get_next_delay()
    assert mgr.attempt_count == 2
    mgr.reset()
    assert mgr.attempt_count == 0


def test_should_retry():
    mgr = ReconnectionManager(ReconnectionConfig(max_attempts=3))
    assert mgr.should_retry()
    mgr.get_next_delay()
    mgr.get_next_delay()
    mgr.get_next_delay()
    assert not mgr.should_retry()


def test_should_retry_resets():
    mgr = ReconnectionManager(ReconnectionConfig(max_attempts=2))
    mgr.get_next_delay()
    mgr.get_next_delay()
    assert not mgr.should_retry()
    mgr.reset()
    assert mgr.should_retry()


@pytest.mark.asyncio
async def test_reconnect_calls_connect():
    """reconnect_with_backoff should call the connect function."""
    connect_fn = AsyncMock(return_value=True)
    mgr = ReconnectionManager(ReconnectionConfig(initial_delay=0.01, max_delay=0.05))

    success = await mgr.reconnect_with_backoff(connect_fn)
    assert success
    connect_fn.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_retries_on_failure():
    """Should retry on failure up to max_attempts."""
    call_count = 0

    async def connect():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("failed")
        return True

    mgr = ReconnectionManager(
        ReconnectionConfig(initial_delay=0.01, max_delay=0.05, max_attempts=5)
    )

    success = await mgr.reconnect_with_backoff(connect)
    assert success
    assert call_count == 3


@pytest.mark.asyncio
async def test_reconnect_gives_up_after_max():
    """Should give up after max_attempts."""
    connect_fn = AsyncMock(side_effect=ConnectionError("always fails"))
    mgr = ReconnectionManager(
        ReconnectionConfig(initial_delay=0.01, max_delay=0.05, max_attempts=3)
    )

    success = await mgr.reconnect_with_backoff(connect_fn)
    assert not success
    assert connect_fn.call_count == 3
