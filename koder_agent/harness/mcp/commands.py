"""Harness MCP subcommand handlers."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from koder_agent.mcp.server_config import MCPAddRequest, MCPServerType
from koder_agent.mcp.server_manager import MCPServerManager

_SECRET_NAME_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api-key",
    "apikey",
    "auth",
    "credential",
    "private-key",
    "access-key",
    "bearer",
)
_ENV_REFERENCE = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")


def _is_secret_name(value: str) -> bool:
    normalized = value.lower().lstrip("-").replace("_", "-")
    return any(part in normalized for part in _SECRET_NAME_PARTS)


def _redact_argv(argv: list[str], raw_argv: list[str] | None = None) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for index, value in enumerate(argv):
        if index == 0:
            redacted.append(value)
            continue
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
            continue
        raw_value = raw_argv[index] if raw_argv is not None and index < len(raw_argv) else ""
        referenced_names = [match.group(1) for match in _ENV_REFERENCE.finditer(raw_value)]
        if any(_is_secret_name(name) for name in referenced_names):
            redacted.append("<redacted>")
            continue
        if "=" in value:
            name, _separator, _secret = value.partition("=")
            if _is_secret_name(name):
                redacted.append(f"{name}=<redacted>")
                continue
        redacted.append(value)
        hide_next = value.startswith("-") and _is_secret_name(value)
    return redacted


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    if parsed.username is not None:
        username = parsed.username
        password = ":<redacted>" if parsed.password is not None else ""
        netloc = f"{username}{password}@{hostname}"
    else:
        netloc = hostname or parsed.netloc
    query = urlencode(
        [
            (name, "<redacted>" if _is_secret_name(name) else value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def _redact_expanded_url(url: str, raw_url: str | None) -> str:
    redacted = _redact_url(url)
    if not raw_url:
        return redacted
    for match in _ENV_REFERENCE.finditer(raw_url):
        name, default = match.groups()
        if not _is_secret_name(name):
            continue
        secret = os.environ.get(name, default)
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def _project_approval_groups(
    manager: MCPServerManager,
    cwd: str,
) -> list[list]:
    groups = list(manager.project_source_config_groups(cwd))

    from koder_agent.harness.agents.definitions import (
        get_agent_definitions,
        resolve_agent_mcp_server_configs,
    )
    from koder_agent.mcp.server_config import MCPServerScope

    definitions = get_agent_definitions(cwd=Path(cwd))
    for agent in definitions.all_agents:
        if agent.source != "projectSettings" or not agent.mcp_servers:
            continue
        project_configs = [
            config
            for config in resolve_agent_mcp_server_configs(agent)
            if config.scope == MCPServerScope.PROJECT
        ]
        if project_configs:
            groups.append(project_configs)

    unique: dict[tuple[str, str, str], list] = {}
    for configs in groups:
        first = configs[0]
        if first.project_root and first.source_path and first.source_digest:
            unique[(first.project_root, first.source_path, first.source_digest)] = configs
    return list(unique.values())


def _print_project_approval_review(configs: list) -> None:
    first = configs[0]
    print("Project MCP source review")
    print(f"  Source: {first.source_path}")
    print(f"  Project root: {first.project_root}")
    print(f"  Execution directory: {first.execution_cwd}")
    print(f"  Expanded configuration digest: {first.source_digest}")
    print("  Servers:")
    for config in configs:
        descriptor = config.execution_descriptor or {}
        source_mapping = (config.source_template or {}).get(config.name) or {}
        print(f"    - {config.name} ({config.transport_type.value})")
        stdio = descriptor.get("stdio")
        if isinstance(stdio, dict):
            executable = str(stdio.get("executable") or config.command or "<missing>")
            argv = [str(value) for value in stdio.get("argv") or []]
            raw_args = source_mapping.get("args") if isinstance(source_mapping, dict) else []
            raw_command = (
                str(source_mapping.get("command") or executable)
                if isinstance(source_mapping, dict)
                else executable
            )
            raw_argv = [raw_command, *[str(value) for value in (raw_args or [])]]
            print(f"      Executable: {executable}")
            print(f"      Argv: {json.dumps(_redact_argv(argv, raw_argv), ensure_ascii=False)}")
            fingerprint = stdio.get("fingerprint")
            if isinstance(fingerprint, dict):
                print(f"      Executable SHA-256: {fingerprint.get('sha256')}")
        elif config.url:
            raw_url = source_mapping.get("url") if isinstance(source_mapping, dict) else None
            print(f"      URL: {_redact_expanded_url(config.url, raw_url)}")
        print(f"      Cwd: {descriptor.get('cwd') or config.execution_cwd}")
        print(f"      PATH: {descriptor.get('path') or '<not applicable>'}")

        helper = descriptor.get("headersHelper")
        if isinstance(helper, dict):
            helper_executable = str(helper.get("executable") or "<missing>")
            helper_argv = [str(value) for value in helper.get("argv") or []]
            raw_helper = (
                source_mapping.get("headersHelper") or source_mapping.get("headers_helper")
                if isinstance(source_mapping, dict)
                else None
            )
            try:
                raw_helper_argv = shlex.split(str(raw_helper), posix=os.name != "nt")
            except ValueError:
                raw_helper_argv = None
            print(f"      headersHelper executable: {helper_executable}")
            print(
                "      headersHelper argv: "
                f"{json.dumps(_redact_argv(helper_argv, raw_helper_argv), ensure_ascii=False)}"
            )
        if config.env_vars:
            print(f"      Environment keys: {', '.join(sorted(map(str, config.env_vars)))}")
        if config.headers:
            print(f"      Header names: {', '.join(sorted(map(str, config.headers)))}")
        if config.oauth:
            print(f"      OAuth fields: {', '.join(sorted(map(str, config.oauth)))}")
    print("  Approval stores only the project root, source path, decision, and digest.")


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

        count = reset_project_choices(project_root=manager.project_boundary(cwd))
        if count:
            print(f"Reset {count} project approval choice(s).")
        else:
            print("No project approval choices to reset.")
        return 0

    if args.mcp_action == "approve":
        from koder_agent.mcp.project_approvals import set_project_approval

        if not bool(getattr(args, "yes", False)) and not sys.stdin.isatty():
            print(
                "Cannot prompt for project MCP approval because stdin is not a TTY. "
                "Review the configuration in an interactive terminal or rerun with --yes."
            )
            return 2

        try:
            groups = _project_approval_groups(manager, cwd)
        except (OSError, ValueError) as exc:
            print(f"Cannot review project MCP sources: {exc}")
            return 1

        requested_sources = {
            str(Path(source).expanduser().resolve())
            for source in (getattr(args, "source", []) or [])
        }
        if requested_sources:
            groups = [
                configs
                for configs in groups
                if str(Path(configs[0].source_path).resolve()) in requested_sources
            ]
        if not groups:
            print("No matching project MCP sources found.")
            return 1 if requested_sources else 0

        approved_count = 0
        for configs in groups:
            _print_project_approval_review(configs)
            approve = bool(getattr(args, "yes", False))
            if not approve:
                try:
                    response = input("Approve this exact expanded digest? [y/N] ").strip().lower()
                except EOFError:
                    print("Approval input closed; no project MCP source was approved.")
                    return 2
                approve = response in {"y", "yes"}
            if not approve:
                print("Not approved.")
                continue
            first = configs[0]
            set_project_approval(
                project_root=first.project_root,
                source_path=first.source_path,
                source_digest=first.source_digest,
                approved=True,
            )
            approved_count += 1
            print(f"Approved current digest for {first.source_path}")

        print(f"Approved {approved_count} project MCP source(s).")
        return 0

    if args.mcp_action == "serve":
        from koder_agent.mcp.serve import start_mcp_server

        await start_mcp_server()
        return 0

    print("Usage: koder mcp <add|add-json|list|get|remove|approve|reset-project-choices|serve>")
    return 0
