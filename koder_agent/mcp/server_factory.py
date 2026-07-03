"""Factory for creating MCP server instances."""

from __future__ import annotations

import asyncio
import json as _json
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable, List, Optional

from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerSseParams,
    MCPServerStdio,
    MCPServerStdioParams,
    MCPServerStreamableHttp,
    MCPServerStreamableHttpParams,
    create_static_tool_filter,
)
from mcp.client.session import ClientSession, ElicitationFnT

from .limits import get_timeout_seconds
from .reconnection import ReconnectionConfig, ReconnectionManager
from .server_config import MCPServerConfig, MCPServerType

logger = logging.getLogger(__name__)

# Type for the channel notification callback
ChannelNotifCallbackT = Callable[[str, str, dict[str, Any]], Awaitable[None]]


def _get_elicitation_callback() -> ElicitationFnT:
    """Return the global elicitation handler (lazy import to avoid cycles)."""
    from .elicitation import get_elicitation_handler

    return get_elicitation_handler()


class ChannelAwareMCPServerStdio(MCPServerStdio):
    """MCPServerStdio that intercepts channel notifications.

    Overrides ``connect()`` to wrap the read stream with a
    ``ChannelInterceptingStream`` that captures
    ``notifications/claude/channel`` and
    ``notifications/claude/channel/permission`` before they reach
    the SDK's ``ServerNotification`` validator.
    """

    def __init__(
        self,
        *args: Any,
        channel_callback: ChannelNotifCallbackT | None = None,
        channel_server_name: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._channel_callback = channel_callback
        self._channel_server_name = channel_server_name

    async def connect(self) -> None:
        """Connect with channel notification interception."""
        if self._channel_callback is None:
            return await super().connect()

        from koder_agent.harness.channels.interceptor import ChannelInterceptingStream

        connection_succeeded = False
        try:
            transport = await self.exit_stack.enter_async_context(self.create_streams())
            read, write, *_ = transport

            # Wrap the read stream to intercept channel notifications
            intercepted_read = ChannelInterceptingStream(
                read,
                on_notification=self._channel_callback,
                server_name=self._channel_server_name,
            )

            session = await self.exit_stack.enter_async_context(
                ClientSession(
                    intercepted_read,
                    write,
                    (
                        timedelta(seconds=self.client_session_timeout_seconds)
                        if self.client_session_timeout_seconds
                        else None
                    ),
                    elicitation_callback=_get_elicitation_callback(),
                    message_handler=self.message_handler,
                )
            )
            server_result = await session.initialize()
            self.server_initialize_result = server_result
            self.session = session
            connection_succeeded = True
        except Exception as e:
            logger.error(f"Error connecting channel-aware MCP server: {e}")
            if not connection_succeeded:
                try:
                    await self.cleanup()
                except Exception:
                    logger.debug("MCP server cleanup failed after connection error", exc_info=True)
            raise


class _ElicitationMixin:
    """Mixin that overrides ``connect()`` to pass the elicitation callback.

    The SDK's ``_MCPServerWithClientSession.connect()`` creates a
    ``ClientSession`` without ``elicitation_callback``.  This mixin replaces
    ``connect()`` so the callback is wired in before ``session.initialize()``
    advertises client capabilities.
    """

    async def connect(self) -> None:  # type: ignore[override]
        connection_succeeded = False
        try:
            transport = await self.exit_stack.enter_async_context(  # type: ignore[attr-defined]
                self.create_streams()  # type: ignore[attr-defined]
            )
            read, write, *_ = transport

            session = await self.exit_stack.enter_async_context(  # type: ignore[attr-defined]
                ClientSession(
                    read,
                    write,
                    (
                        timedelta(seconds=self.client_session_timeout_seconds)  # type: ignore[attr-defined]
                        if self.client_session_timeout_seconds  # type: ignore[attr-defined]
                        else None
                    ),
                    elicitation_callback=_get_elicitation_callback(),
                    message_handler=self.message_handler,  # type: ignore[attr-defined]
                )
            )
            server_result = await session.initialize()
            self.server_initialize_result = server_result  # type: ignore[attr-defined]
            self.session = session  # type: ignore[attr-defined]
            connection_succeeded = True
        except Exception as e:
            logger.error(f"Error connecting MCP server with elicitation: {e}")
            if not connection_succeeded:
                try:
                    await self.cleanup()  # type: ignore[attr-defined]
                except Exception:
                    logger.debug("MCP elicitation server cleanup failed", exc_info=True)
            raise


class ElicitationAwareStdio(_ElicitationMixin, MCPServerStdio):
    """MCPServerStdio with elicitation support."""


class ElicitationAwareSse(_ElicitationMixin, MCPServerSse):
    """MCPServerSse with elicitation support."""


class ElicitationAwareHttp(_ElicitationMixin, MCPServerStreamableHttp):
    """MCPServerStreamableHttp with elicitation support."""


HEADERS_HELPER_TIMEOUT_S = 10.0


async def _resolve_headers_helper(helper_cmd: str) -> dict[str, str]:
    """Run a headersHelper shell command and return the parsed JSON headers.

    The command must write a JSON object of string key-value pairs to stdout.
    It runs in a shell with a 10-second timeout.  On any error the result is
    an empty dict so that the connection attempt can still proceed with the
    static headers only.
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            helper_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=HEADERS_HELPER_TIMEOUT_S
        )
        if proc.returncode != 0:
            logger.warning(
                "headersHelper returned exit code %d: %s",
                proc.returncode,
                stderr.decode(errors="replace").strip()[:200],
            )
            return {}
        parsed = _json.loads(stdout.decode())
        if not isinstance(parsed, dict):
            logger.warning("headersHelper output is not a JSON object")
            return {}
        return {str(k): str(v) for k, v in parsed.items()}
    except asyncio.TimeoutError:
        logger.warning("headersHelper timed out after %.0fs", HEADERS_HELPER_TIMEOUT_S)
        return {}
    except (_json.JSONDecodeError, Exception) as exc:
        logger.warning("headersHelper failed: %s", exc)
        return {}


async def _build_effective_headers(config: MCPServerConfig) -> dict[str, str]:
    """Merge static headers, OAuth headers, and dynamic headersHelper output.

    Layer order (later overrides earlier):
    1. Static ``config.headers``
    2. OAuth ``Authorization`` header (if ``config.oauth`` is set)
    3. Dynamic ``headersHelper`` output
    """
    headers = dict(config.headers or {})

    if config.oauth and config.url:
        from .oauth import resolve_oauth_headers

        try:
            oauth_headers = await resolve_oauth_headers(config.name, config.url, config.oauth)
            headers.update(oauth_headers)
        except Exception as exc:
            logger.warning("OAuth flow failed for '%s': %s", config.name, exc)

    if config.headers_helper:
        dynamic = await _resolve_headers_helper(config.headers_helper)
        headers.update(dynamic)
    return headers


class MCPServerFactory:
    """Factory for creating MCP server instances from configurations."""

    @staticmethod
    async def create_server(
        config: MCPServerConfig,
        channel_callback: ChannelNotifCallbackT | None = None,
    ) -> MCPServer:
        """Create an MCP server instance from configuration.

        If *channel_callback* is provided and the transport is stdio,
        a :class:`ChannelAwareMCPServerStdio` is used instead of the
        standard ``MCPServerStdio`` so that ``notifications/claude/channel``
        messages are intercepted before the SDK drops them.

        For SSE/HTTP servers with a ``headersHelper``, the helper command
        is executed and its JSON output is merged into the connection headers.
        """
        try:
            # Create tool filter if specified
            tool_filter = None
            if config.allowed_tools or config.blocked_tools:
                tool_filter = create_static_tool_filter(
                    allowed_tool_names=config.allowed_tools,
                    blocked_tool_names=config.blocked_tools,
                )

            if config.transport_type == MCPServerType.STDIO:
                return MCPServerFactory._create_stdio_server(
                    config, tool_filter, channel_callback=channel_callback
                )
            elif config.transport_type == MCPServerType.SSE:
                return await MCPServerFactory._create_sse_server(config, tool_filter)
            elif config.transport_type == MCPServerType.HTTP:
                return await MCPServerFactory._create_http_server(config, tool_filter)
            else:
                raise ValueError(f"Unsupported transport type: {config.transport_type}")

        except Exception as e:
            logger.error(f"Failed to create MCP server '{config.name}': {e}")
            raise

    @staticmethod
    def _create_stdio_server(
        config: MCPServerConfig,
        tool_filter: Any = None,
        channel_callback: ChannelNotifCallbackT | None = None,
    ) -> MCPServerStdio:
        """Create a stdio MCP server."""
        if not config.command:
            raise ValueError("stdio server requires a command")

        params = MCPServerStdioParams(
            command=config.command,
            args=config.args or [],
            env=config.env_vars or {},
        )

        if channel_callback is not None:
            return ChannelAwareMCPServerStdio(
                params=params,
                client_session_timeout_seconds=get_timeout_seconds(),
                tool_filter=tool_filter,
                cache_tools_list=config.cache_tools_list,
                channel_callback=channel_callback,
                channel_server_name=config.name,
            )

        return ElicitationAwareStdio(
            params=params,
            client_session_timeout_seconds=get_timeout_seconds(),
            tool_filter=tool_filter,
            cache_tools_list=config.cache_tools_list,
        )

    @staticmethod
    async def _create_sse_server(config: MCPServerConfig, tool_filter) -> MCPServerSse:
        """Create an SSE MCP server."""
        if not config.url:
            raise ValueError("SSE server requires a URL")

        effective_headers = await _build_effective_headers(config)

        params = MCPServerSseParams(
            url=config.url,
            headers=effective_headers,
            timeout=get_timeout_seconds(),
        )

        return ElicitationAwareSse(
            params=params,
            tool_filter=tool_filter,
            cache_tools_list=config.cache_tools_list,
        )

    @staticmethod
    async def _create_http_server(config: MCPServerConfig, tool_filter) -> MCPServerStreamableHttp:
        """Create an HTTP MCP server."""
        if not config.url:
            raise ValueError("HTTP server requires a URL")

        effective_headers = await _build_effective_headers(config)

        params = MCPServerStreamableHttpParams(
            url=config.url,
            headers=effective_headers,
            timeout=get_timeout_seconds(),
        )

        return ElicitationAwareHttp(
            params=params,
            tool_filter=tool_filter,
            cache_tools_list=config.cache_tools_list,
        )

    @staticmethod
    async def create_servers_from_configs(
        configs: List[MCPServerConfig],
    ) -> List[MCPServer]:
        """Create multiple MCP server instances from configurations."""
        servers = []
        for config in configs:
            try:
                server = await MCPServerFactory.create_server(config)
                await server.connect()
                servers.append(server)
                logger.info(f"Created MCP server '{config.name}' ({config.transport_type})")
            except Exception as e:
                logger.error(f"Failed to create MCP server '{config.name}': {e}")
                # Continue with other servers even if one fails
                continue

        return servers

    @staticmethod
    async def create_and_connect_with_retry(
        config: MCPServerConfig,
        channel_callback: Any = None,
        reconnection_config: Optional[ReconnectionConfig] = None,
    ) -> tuple[MCPServer, ReconnectionManager]:
        """Create and connect an MCP server with reconnection support.

        Returns:
            A tuple of (server, reconnection_manager) where the manager can be
            used for future reconnection attempts.
        """
        reconnection_mgr = ReconnectionManager(reconnection_config)

        async def connect_fn():
            server = await MCPServerFactory.create_server(config, channel_callback)
            await server.connect()
            return server

        # Try initial connection with retry
        server = None
        success = False

        async def retry_connect():
            nonlocal server
            server = await connect_fn()

        success = await reconnection_mgr.reconnect_with_backoff(retry_connect)

        if not success or server is None:
            raise ConnectionError(
                f"Failed to connect to MCP server '{config.name}' after "
                f"{reconnection_mgr.config.max_attempts} attempts"
            )

        return server, reconnection_mgr

    @staticmethod
    def validate_config(config: MCPServerConfig) -> Optional[str]:
        """Validate an MCP server configuration."""
        try:
            if config.transport_type == MCPServerType.STDIO:
                if not config.command:
                    return "stdio servers must have a command"
            elif config.transport_type in [MCPServerType.SSE, MCPServerType.HTTP]:
                if not config.url:
                    return f"{config.transport_type} servers must have a URL"
                if not config.url.startswith(("http://", "https://")):
                    return "URL must start with http:// or https://"
            else:
                return f"Unsupported transport type: {config.transport_type}"

            # Validate tool lists don't overlap
            if config.allowed_tools and config.blocked_tools:
                overlap = set(config.allowed_tools) & set(config.blocked_tools)
                if overlap:
                    return f"Tools cannot be in both allowed and blocked lists: {list(overlap)}"

            return None
        except Exception as e:
            return f"Configuration validation error: {e}"
