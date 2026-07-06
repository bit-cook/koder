"""MCP (Model Context Protocol) support for Koder."""

import os
from typing import Any, List

from rich.console import Console

try:  # pragma: no cover - depends on optional SDK extras at import time
    from agents.mcp import MCPServer
except ImportError:  # pragma: no cover
    MCPServer = Any

from .prompts import (
    MCPPrompt,
    MCPPromptRegistry,
    execute_prompt,
    get_prompt_registry,
    normalize_mcp_name,
)
from .reconnection import ReconnectionConfig
from .server_config import MCPServerConfig, MCPServerType
from .server_manager import MCPServerManager

try:  # pragma: no cover - optional transport dependencies may be absent
    from .server_factory import MCPServerFactory
except ImportError:  # pragma: no cover
    MCPServerFactory = None  # type: ignore[assignment]

# Dedicated stderr console so MCP-connection warnings reach the user even when
# the root logger is pinned high elsewhere.
_console = Console(stderr=True)


def _load_plugin_mcp_configs() -> List[MCPServerConfig]:
    """Load MCP server configs from enabled plugins' .mcp.json files."""
    import json
    import logging
    from pathlib import Path

    from koder_agent.harness.plugins.env import expand_plugin_vars, plugin_env_vars
    from koder_agent.harness.plugins.lifecycle import PluginLifecycleService

    _logger = logging.getLogger(__name__)
    configs: List[MCPServerConfig] = []

    try:
        plugin_root = Path.home() / ".koder" / "plugins"
        lifecycle = PluginLifecycleService(plugin_root)
        for manifest, state in lifecycle.installed_plugins():
            if not state.enabled:
                continue
            plugin_dir = lifecycle.root / manifest.name
            mcp_json_path = plugin_dir / ".mcp.json"
            if not mcp_json_path.is_file():
                continue

            try:
                raw = json.loads(mcp_json_path.read_text("utf-8"))
                servers = raw.get("mcpServers", {})
                env_vars = plugin_env_vars(manifest.name, plugin_dir)

                for server_name, server_def in servers.items():
                    command = server_def.get("command", "")
                    args = server_def.get("args", [])

                    # Expand Koder plugin variables.
                    command = expand_plugin_vars(command, manifest.name, plugin_dir)
                    args = [expand_plugin_vars(a, manifest.name, plugin_dir) for a in args]

                    # Merge plugin env vars into server env
                    server_env = dict(server_def.get("env", {}))
                    server_env.update(env_vars)

                    # Channel plugins use Koder-owned state paths by default.
                    koder_channels_dir = Path.home() / ".koder" / "channels" / server_name
                    state_dir_key = f"{server_name.upper()}_STATE_DIR"
                    if state_dir_key not in server_env:
                        server_env[state_dir_key] = str(koder_channels_dir)

                    transport_type = server_def.get("type", "stdio")
                    config = MCPServerConfig(
                        name=server_name,
                        transport_type=MCPServerType(transport_type),
                        command=command,
                        args=args,
                        env_vars=server_env,
                        url=server_def.get("url"),
                        headers=server_def.get("headers"),
                        cache_tools_list=server_def.get("cacheToolsList", False),
                        source_path=str(mcp_json_path),
                    )
                    configs.append(config)
                    _logger.info(
                        "Loaded MCP server '%s' from plugin '%s'",
                        server_name,
                        manifest.name,
                    )
            except Exception as exc:
                _logger.warning(
                    "Failed to load .mcp.json from plugin '%s': %s",
                    manifest.name,
                    exc,
                )
    except Exception as exc:
        _logger.debug("Plugin MCP loading skipped: %s", exc)

    return configs


async def load_mcp_servers() -> List[MCPServer]:
    """Load and create MCP server instances from configuration."""
    import logging

    _logger = logging.getLogger(__name__)

    try:
        manager = MCPServerManager()
        configs = await manager.list_servers(cwd=os.getcwd())

        # Also load MCP servers from enabled plugins
        plugin_configs = _load_plugin_mcp_configs()
        if plugin_configs:
            configs = list(configs) + plugin_configs

        if not configs:
            return []

        # Determine which servers need channel notification interception
        channel_callback = None
        channel_server_names: set[str] = set()
        try:
            from koder_agent.harness.channels.gate import gate_channel_server
            from koder_agent.harness.channels.notification import ChannelNotificationRouter
            from koder_agent.harness.channels.state import get_allowed_channels

            allowed = get_allowed_channels()
            if allowed:
                # Pre-scan configs to identify which servers will be channels
                # (we can't check capabilities yet — the server hasn't connected)
                # Instead, we tag all servers in the --channels list
                from koder_agent.harness.channels.gate import find_channel_entry

                for config in configs:
                    if find_channel_entry(config.name, allowed) is not None:
                        channel_server_names.add(config.name)

                if channel_server_names:
                    from .notifications import get_notification_handler

                    handler = get_notification_handler()
                    # Reuse existing router if session_flow already created one
                    router = handler.channel_router or ChannelNotificationRouter()
                    if handler.channel_router is None:
                        handler.set_channel_router(router)

                    async def _channel_callback(
                        server_name: str, method: str, params: dict
                    ) -> None:
                        await router.dispatch_raw_notification(server_name, method, params)

                    channel_callback = _channel_callback
        except ImportError:
            pass

        # Create server instances — channel-aware for those in --channels
        if MCPServerFactory is None:
            raise RuntimeError("MCP transport dependencies are unavailable")

        _logger.debug(
            "Channel server names: %s, callback set: %s",
            channel_server_names,
            channel_callback is not None,
        )
        # Configure reconnection with retry
        reconnection_config = ReconnectionConfig(max_attempts=3, initial_delay=1.0, max_delay=10.0)
        servers: List[MCPServer] = []
        connected: list[tuple[MCPServerConfig, MCPServer]] = []
        for config in configs:
            try:
                cb = channel_callback if config.name in channel_server_names else None
                _logger.debug(
                    "Creating server '%s': channel_callback=%s",
                    config.name,
                    cb is not None,
                )
                # Use create_and_connect_with_retry for resilient connection
                server, _reconnection_mgr = await MCPServerFactory.create_and_connect_with_retry(
                    config, channel_callback=cb, reconnection_config=reconnection_config
                )
                _logger.debug(
                    "Server '%s' class: %s",
                    config.name,
                    type(server).__name__,
                )
                servers.append(server)
                connected.append((config, server))
                if config.name in channel_server_names:
                    # Verify capability after connection
                    caps = getattr(server, "server_initialize_result", None)
                    if caps is not None:
                        caps = getattr(caps, "capabilities", caps)
                    result = gate_channel_server(config.name, caps)
                    if result.action == "register":
                        _logger.info("Channel registered: '%s'", config.name)
                    else:
                        _logger.debug(
                            "Channel skipped for '%s': %s (%s)",
                            config.name,
                            result.kind,
                            result.reason,
                        )
                _logger.info(f"Created MCP server '{config.name}' ({config.transport_type})")
            except Exception as e:
                _logger.error(f"Failed to create MCP server '{config.name}': {e}")
                # Surface the failure to the user — the root logger is pinned
                # high elsewhere, so a bare _logger.error() would be invisible.
                _console.print(f"[yellow]⚠ MCP server '{config.name}' unavailable: {e}[/yellow]")
                continue

        # Try to discover prompts from connected servers
        # Iterate the (config, server) pairs we actually connected, rather than
        # zip(configs, servers): a skipped server would misalign the zip and
        # attribute prompts to the wrong config after the first failure.
        registry = get_prompt_registry()
        registry.clear()
        for config, server in connected:
            try:
                await _discover_prompts(config.name, server, registry)
            except Exception:
                pass  # Prompt discovery is best-effort

        return servers

    except Exception as e:
        raise RuntimeError(f"Failed to load MCP servers: {e}") from e


async def _discover_prompts(
    server_name: str,
    server: MCPServer,
    registry: MCPPromptRegistry,
) -> None:
    """Try to discover prompts from an MCP server."""
    # The agents library MCPServer may expose session for direct MCP calls
    session = getattr(server, "session", None)
    if session is None:
        return

    try:
        result = await session.list_prompts()
        for prompt_info in getattr(result, "prompts", []):
            prompt = MCPPrompt(
                server_name=server_name,
                prompt_name=prompt_info.name,
                description=getattr(prompt_info, "description", "") or "",
                arguments=[
                    {
                        "name": arg.name,
                        "required": getattr(arg, "required", False),
                    }
                    for arg in (getattr(prompt_info, "arguments", None) or [])
                ],
            )
            registry.register(prompt)
    except Exception:
        pass  # Server may not support prompts


async def discover_mcp_resources(
    servers: List[MCPServer],
) -> List[tuple[str, str]]:
    """Discover resources from connected MCP servers.

    Returns a list of ``(display_uri, description)`` tuples suitable for
    the ``AtMentionCompleter.update_mcp_resources()`` method.  The
    *display_uri* uses the ``server:protocol://path`` format so users can
    reference them as ``@server:protocol://path`` in prompts.
    """
    import logging

    _logger = logging.getLogger(__name__)
    results: List[tuple[str, str]] = []

    for server in servers:
        session = getattr(server, "session", None)
        if session is None:
            continue
        server_name = getattr(server, "name", None)
        if not server_name:
            continue
        try:
            response = await session.list_resources()
            for resource in getattr(response, "resources", []):
                uri = str(resource.uri)
                description = getattr(resource, "description", "") or getattr(resource, "name", "")
                display_uri = f"{server_name}:{uri}"
                results.append((display_uri, description))
        except Exception as exc:
            _logger.debug(
                "Resource discovery skipped for server '%s': %s",
                server_name,
                exc,
            )

    return results


__all__ = [
    "load_mcp_servers",
    "discover_mcp_resources",
    "MCPPrompt",
    "MCPPromptRegistry",
    "MCPServerConfig",
    "MCPServerType",
    "MCPServerManager",
    "MCPServerFactory",
    "execute_prompt",
    "get_prompt_registry",
    "normalize_mcp_name",
]
