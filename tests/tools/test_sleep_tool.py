"""Tests for the sleep tool."""

import time

import pytest

from koder_agent.tools.sleep import sleep_tool


@pytest.mark.anyio
async def test_sleep_basic():
    """Sleep for 1 second and verify timing."""
    start = time.monotonic()
    result = await sleep_tool.on_invoke_tool(None, '{"seconds": 1}')
    elapsed = time.monotonic() - start
    assert "1 seconds" in result
    assert elapsed >= 0.9


@pytest.mark.anyio
async def test_sleep_default():
    """Default sleep is 5 seconds — just verify the message, don't wait."""
    # We test the return format by calling with explicit 2s instead of waiting 5s
    result = await sleep_tool.on_invoke_tool(None, '{"seconds": 2}')
    assert "2 seconds" in result


@pytest.mark.anyio
async def test_sleep_clamps_minimum():
    """Values below 1 should be clamped to 1."""
    start = time.monotonic()
    result = await sleep_tool.on_invoke_tool(None, '{"seconds": 0}')
    elapsed = time.monotonic() - start
    assert "1 seconds" in result
    assert elapsed >= 0.9


@pytest.mark.anyio
async def test_sleep_clamps_maximum():
    """Values above 300 should be clamped to 300 in the message."""
    # We can't actually sleep 300s in a test; verify via the return value.
    # Use a mock to avoid the actual sleep.
    from unittest.mock import AsyncMock, patch

    with patch("koder_agent.tools.sleep.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await sleep_tool.on_invoke_tool(None, '{"seconds": 999}')
        assert "300 seconds" in result
        mock_sleep.assert_awaited_once_with(300)


@pytest.mark.anyio
async def test_sleep_negative_clamped():
    """Negative values should be clamped to 1."""
    from unittest.mock import AsyncMock, patch

    with patch("koder_agent.tools.sleep.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await sleep_tool.on_invoke_tool(None, '{"seconds": -5}')
        assert "1 seconds" in result
        mock_sleep.assert_awaited_once_with(1)
