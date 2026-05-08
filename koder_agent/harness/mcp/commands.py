"""Harness MCP subcommand handlers."""

from __future__ import annotations

import argparse
import os

from koder_agent.mcp.server_config import MCPAddRequest, MCPServerType
from koder_agent.mcp.server_manager import MCPServerManager


async def handle_mcp_subcommand(args: argparse.Namespace) -> int:
    manager = MCPServerManager()
    cwd = os.getcwd()

    if args.mcp_action == "list":
        servers = await manager.list_servers(cwd=cwd)
        if not servers:
            print("No MCP servers configured.")
            return 0
        for server in servers:
            target = server.url or f"{server.command} {' '.join(server.args or [])}".strip()
            scope = getattr(server.scope, "value", server.scope) or "unknown"
            print(f"{server.name}\t{server.transport_type.value}\t{scope}\t{target}")
        return 0

    if args.mcp_action == "get":
        server = await manager.get_server(args.name, cwd=cwd, scope=getattr(args, "scope", None))
        if server is None:
            print(f"Server not found: {args.name}")
            return 1
        print(server.model_dump_json(indent=2))
        return 0

    if args.mcp_action == "remove":
        removed = await manager.remove_server(
            args.name, cwd=cwd, scope=getattr(args, "scope", None)
        )
        if not removed:
            print(f"Server not found: {args.name}")
            return 1
        print(f"Removed MCP server: {args.name}")
        return 0

    if args.mcp_action == "add":
        env_vars = {}
        for item in args.env or []:
            key, value = item.split("=", 1)
            env_vars[key] = value
        headers = {}
        for item in args.header or []:
            key, value = item.split(":", 1)
            headers[key.strip()] = value.strip()

        # Build OAuth config from CLI flags and env vars
        oauth_dict = None
        client_id = getattr(args, "client_id", None)
        client_secret = getattr(args, "client_secret", None) or os.environ.get("MCP_CLIENT_SECRET")
        callback_port = getattr(args, "callback_port", None) or (
            int(os.environ.get("MCP_OAUTH_CALLBACK_PORT", "0")) or None
        )
        if client_id or client_secret or callback_port:
            oauth_dict = {}
            if client_id:
                oauth_dict["clientId"] = client_id
            if client_secret:
                oauth_dict["clientSecret"] = client_secret
            if callback_port:
                oauth_dict["callbackPort"] = callback_port

        request = MCPAddRequest(
            name=args.name,
            transport_type=MCPServerType(args.transport),
            command=args.command_or_url if args.transport == "stdio" else None,
            args=args.args if args.transport == "stdio" else [],
            env_vars=env_vars,
            url=args.command_or_url if args.transport != "stdio" else None,
            headers=headers,
            oauth=oauth_dict,
            cache_tools_list=args.cache_tools,
            allowed_tools=args.allow_tool,
            blocked_tools=args.block_tool,
        )
        await manager.add_server(request.to_server_config(), scope=args.scope, cwd=cwd)
        print(f"Added MCP server: {args.name}")
        return 0

    if args.mcp_action == "add-json":
        await manager.import_json_server(args.name, args.json, scope=args.scope, cwd=cwd)
        print(f"Added MCP server: {args.name}")
        return 0

    if args.mcp_action == "reset-project-choices":
        from koder_agent.mcp.project_approvals import reset_project_choices

        count = reset_project_choices(project_root=cwd)
        if count:
            print(f"Reset {count} project approval choice(s).")
        else:
            print("No project approval choices to reset.")
        return 0

    if args.mcp_action == "serve":
        from koder_agent.mcp.serve import start_mcp_server

        await start_mcp_server()
        return 0

    print("Usage: koder mcp <add|add-json|list|get|remove|reset-project-choices|serve>")
    return 0
