"""Integration tests for MCP reconnection in server manager."""

from unittest.mock import MagicMock, patch

import pytest

from koder_agent.mcp.reconnection import ReconnectionConfig, ReconnectionManager
from koder_agent.mcp.server_config import MCPServerConfig, MCPServerScope, MCPServerType
from koder_agent.mcp.server_factory import MCPServerFactory


class MockMCPServer:
    """Mock MCP server for testing."""

    def __init__(self, name: str, should_fail: int = 0):
        self.name = name
        self._connect_count = 0
        self._should_fail = should_fail
        self.session = MagicMock()
        self.server_initialize_result = MagicMock()

    async def connect(self):
        """Mock connect that can simulate failures."""
        self._connect_count += 1
        if self._connect_count <= self._should_fail:
            raise ConnectionError(f"Mock connection failure {self._connect_count}")

    async def cleanup(self):
        """Mock cleanup."""
        pass


@pytest.mark.asyncio
async def test_reconnection_manager_created_per_server():
    """ReconnectionManager should be created per server."""
    # Create a reconnection manager for a server
    reconnection_mgr = ReconnectionManager(
        ReconnectionConfig(initial_delay=0.01, max_delay=0.05, max_attempts=3)
    )

    assert reconnection_mgr.attempt_count == 0
    assert reconnection_mgr.should_retry()


@pytest.mark.asyncio
async def test_successful_reconnection():
    """Reconnection should succeed after transient failures."""
    mock_server = MockMCPServer("test-server", should_fail=2)

    async def connect_fn():
        await mock_server.connect()

    reconnection_mgr = ReconnectionManager(
        ReconnectionConfig(initial_delay=0.01, max_delay=0.05, max_attempts=5)
    )

    success = await reconnection_mgr.reconnect_with_backoff(connect_fn)
    assert success
    assert mock_server._connect_count == 3  # Failed 2, succeeded on 3rd


@pytest.mark.asyncio
async def test_reconnection_resets_failure_counter():
    """Successful reconnection should reset the failure counter."""
    mock_server = MockMCPServer("test-server", should_fail=1)

    async def connect_fn():
        await mock_server.connect()

    reconnection_mgr = ReconnectionManager(
        ReconnectionConfig(initial_delay=0.01, max_delay=0.05, max_attempts=5)
    )

    # First reconnection succeeds after 1 failure
    success = await reconnection_mgr.reconnect_with_backoff(connect_fn)
    assert success
    assert reconnection_mgr.attempt_count == 2

    # Reset should clear the counter
    reconnection_mgr.reset()
    assert reconnection_mgr.attempt_count == 0


@pytest.mark.asyncio
async def test_max_failures_marks_server_unavailable():
    """After max failures, reconnection should give up."""
    mock_server = MockMCPServer("test-server", should_fail=999)

    async def connect_fn():
        await mock_server.connect()

    reconnection_mgr = ReconnectionManager(
        ReconnectionConfig(initial_delay=0.01, max_delay=0.05, max_attempts=3)
    )

    success = await reconnection_mgr.reconnect_with_backoff(connect_fn)
    assert not success
    assert mock_server._connect_count == 3  # Tried max_attempts times


@pytest.mark.asyncio
async def test_server_factory_with_retry_success():
    """ServerFactory.create_and_connect_with_retry should succeed with transient errors."""
    config = MCPServerConfig(
        name="test-server",
        transport_type=MCPServerType.STDIO,
        command="echo",
        args=["test"],
        env_vars={},
        scope=MCPServerScope.USER,
        source_path="/tmp/test",
    )

    call_count = 0
    mock_server = MockMCPServer("test-server")

    async def mock_create_server(cfg, channel_callback=None):
        return mock_server

    # Patch server creation to fail first 2 times
    async def failing_create(cfg, channel_callback=None):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise ConnectionError("Transient failure")
        return await mock_create_server(cfg, channel_callback)

    with patch.object(MCPServerFactory, "create_server", failing_create):
        reconnection_config = ReconnectionConfig(initial_delay=0.01, max_delay=0.05, max_attempts=5)

        server, reconnection_mgr = await MCPServerFactory.create_and_connect_with_retry(
            config, reconnection_config=reconnection_config
        )

        assert server is not None
        assert server.name == "test-server"
        assert reconnection_mgr.attempt_count == 3  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_server_factory_with_retry_max_failures():
    """ServerFactory.create_and_connect_with_retry should raise after max attempts."""
    config = MCPServerConfig(
        name="test-server",
        transport_type=MCPServerType.STDIO,
        command="echo",
        args=["test"],
        env_vars={},
        scope=MCPServerScope.USER,
        source_path="/tmp/test",
    )

    async def always_fail(cfg, channel_callback=None):
        raise ConnectionError("Always fails")

    with patch.object(MCPServerFactory, "create_server", always_fail):
        reconnection_config = ReconnectionConfig(initial_delay=0.01, max_delay=0.05, max_attempts=3)

        with pytest.raises(ConnectionError, match="after 3 attempts"):
            await MCPServerFactory.create_and_connect_with_retry(
                config, reconnection_config=reconnection_config
            )


@pytest.mark.asyncio
async def test_server_factory_returns_reconnection_manager():
    """ServerFactory should return a reconnection manager for future use."""
    config = MCPServerConfig(
        name="test-server",
        transport_type=MCPServerType.STDIO,
        command="echo",
        args=["test"],
        env_vars={},
        scope=MCPServerScope.USER,
        source_path="/tmp/test",
    )

    mock_server = MockMCPServer("test-server")

    async def mock_create_server(cfg, channel_callback=None):
        return mock_server

    with patch.object(MCPServerFactory, "create_server", mock_create_server):
        reconnection_config = ReconnectionConfig(initial_delay=0.01, max_attempts=3)

        server, reconnection_mgr = await MCPServerFactory.create_and_connect_with_retry(
            config, reconnection_config=reconnection_config
        )

        assert reconnection_mgr is not None
        assert isinstance(reconnection_mgr, ReconnectionManager)
        assert reconnection_mgr.config.initial_delay == 0.01
        assert reconnection_mgr.config.max_attempts == 3
