"""Command-line interface for Koder Agent."""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from rich.panel import Panel

from .mcp.server_config import MCPServerScope
from .utils.terminal_theme import get_adaptive_console

logging.basicConfig(level=logging.FATAL)
console = get_adaptive_console()


async def _prompt_select_session() -> Optional[str]:
    from .core.session import EnhancedSQLiteSession
    from .utils import parse_session_dt, picker_arrows_with_titles

    sessions_with_titles = await EnhancedSQLiteSession.list_sessions_with_titles()
    if not sessions_with_titles:
        console.print(Panel("No sessions found.", title="Sessions", border_style="yellow"))
        return None

    # Sort by datetime descending
    sessions_with_titles.sort(
        key=lambda x: (parse_session_dt(x[0])[0], parse_session_dt(x[0])[1] or None),
        reverse=True,
    )

    return picker_arrows_with_titles(sessions_with_titles)


async def load_context() -> str:
    """Load context information from the project directory.

    Returns:
        str: The loaded context information.
    """
    context_info = []
    current_dir = os.getcwd()
    context_info.append(f"Working directory: {current_dir}")
    koder_md_path = Path(current_dir) / "AGENTS.md"
    if koder_md_path.exists():
        try:
            from .utils.prompts import resolve_includes

            koder_content = koder_md_path.read_text("utf-8", errors="ignore")
            koder_content = resolve_includes(koder_content, base_dir=koder_md_path.parent)
            context_info.append(f"AGENTS.md content:\n{koder_content}")
        except Exception as e:
            context_info.append(f"Error reading AGENTS.md: {e}")
    return "\n\n".join(context_info)


def create_mcp_subparsers(subparsers):
    """Create MCP subcommand parsers."""
    mcp_parser = subparsers.add_parser("mcp", help="Manage MCP servers")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_action", help="MCP actions")

    add_parser = mcp_subparsers.add_parser("add", help="Add an MCP server")
    add_parser.add_argument("name", help="Server name")
    add_parser.add_argument("command_or_url", help="Command for stdio or URL for SSE/HTTP")
    add_parser.add_argument("args", nargs="*", help="Arguments for stdio command")
    add_parser.add_argument(
        "--transport", choices=["stdio", "sse", "http"], default="stdio", help="Transport type"
    )
    add_parser.add_argument(
        "--scope",
        choices=[scope.value for scope in MCPServerScope],
        default=MCPServerScope.LOCAL.value,
        help="Configuration scope (local, project, or user)",
    )
    add_parser.add_argument(
        "-e", "--env", action="append", help="Environment variables (KEY=VALUE)"
    )
    add_parser.add_argument("--header", action="append", help="HTTP headers (Key: Value)")
    add_parser.add_argument("--cache-tools", action="store_true", help="Cache tools list")
    add_parser.add_argument("--allow-tool", action="append", help="Allowed tools")
    add_parser.add_argument("--block-tool", action="append", help="Blocked tools")
    add_parser.add_argument(
        "--client-id", default=None, help="OAuth client ID for remote server auth"
    )
    add_parser.add_argument(
        "--client-secret",
        default=None,
        help="OAuth client secret (prefer MCP_CLIENT_SECRET env var)",
    )
    add_parser.add_argument(
        "--callback-port",
        type=int,
        default=None,
        help="Fixed port for OAuth redirect URI (default: random free port)",
    )

    add_json_parser = mcp_subparsers.add_parser("add-json", help="Add an MCP server from JSON")
    add_json_parser.add_argument("name", help="Server name")
    add_json_parser.add_argument("json", help="JSON server configuration")
    add_json_parser.add_argument(
        "--scope",
        choices=[scope.value for scope in MCPServerScope],
        default=MCPServerScope.LOCAL.value,
        help="Configuration scope (local, project, or user)",
    )

    mcp_subparsers.add_parser("list", help="List all MCP servers")

    get_parser = mcp_subparsers.add_parser("get", help="Get details for a specific server")
    get_parser.add_argument("name", help="Server name")
    get_parser.add_argument(
        "--scope",
        choices=[scope.value for scope in MCPServerScope],
        default=None,
        help="Optional configuration scope filter",
    )

    remove_parser = mcp_subparsers.add_parser("remove", help="Remove an MCP server")
    remove_parser.add_argument("name", help="Server name")
    remove_parser.add_argument(
        "--scope",
        choices=[scope.value for scope in MCPServerScope],
        default=None,
        help="Optional configuration scope filter",
    )

    mcp_subparsers.add_parser(
        "reset-project-choices",
        help="Reset approval choices for project-scoped MCP servers",
    )

    mcp_subparsers.add_parser("serve", help="Start koder as an MCP server")


def create_config_subparsers(subparsers):
    """Create config subcommand parsers."""
    config_parser = subparsers.add_parser("config", help="Manage configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_action", help="Config actions")

    config_subparsers.add_parser("show", help="Show current configuration")
    config_subparsers.add_parser("list", help="List current configuration")
    config_subparsers.add_parser("path", help="Show config file path")
    config_subparsers.add_parser("edit", help="Open config file in default editor")
    config_subparsers.add_parser("init", help="Initialize config file with defaults")

    export_parser = config_subparsers.add_parser("export", help="Export a local settings bundle")
    export_parser.add_argument("path", help="Output JSON bundle path")
    export_parser.add_argument(
        "--scope",
        choices=["all", "user", "project"],
        default="all",
        help="Settings scope to export",
    )

    import_parser = config_subparsers.add_parser("import", help="Import a local settings bundle")
    import_parser.add_argument("path", help="Input JSON bundle path")
    import_parser.add_argument(
        "--scope",
        choices=["all", "user", "project"],
        default="all",
        help="Settings scope to import",
    )
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show how many files would be written without changing files",
    )

    set_parser = config_subparsers.add_parser("set", help="Set a configuration value")
    set_parser.add_argument("key", help="Configuration key (e.g., model.name)")
    set_parser.add_argument("value", help="Value to set")


def create_agents_subparsers(subparsers):
    """Create agents subcommand parser."""
    subparsers.add_parser("agents", help="List configured agents")


def create_plugin_subparsers(subparsers):
    """Create plugin subcommand parsers."""
    plugin_parser = subparsers.add_parser(
        "plugin", aliases=["plugins"], help="Manage installed plugins"
    )
    plugin_subparsers = plugin_parser.add_subparsers(dest="plugin_action", help="Plugin actions")

    # koder plugin list [--json]
    list_parser = plugin_subparsers.add_parser("list", help="List installed plugins")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # koder plugin install <plugin> [--scope user|project|local]
    install_parser = plugin_subparsers.add_parser(
        "install", help="Install a plugin from a directory or marketplace"
    )
    install_parser.add_argument("plugin_ref", help="Plugin directory path or name@marketplace")
    install_parser.add_argument(
        "--scope",
        choices=["user", "project", "local"],
        default="user",
        help="Installation scope (default: user)",
    )

    # koder plugin uninstall <name>
    uninstall_parser = plugin_subparsers.add_parser(
        "uninstall", aliases=["remove", "rm"], help="Uninstall a plugin"
    )
    uninstall_parser.add_argument("name", help="Plugin name")

    # koder plugin enable <name>
    enable_parser = plugin_subparsers.add_parser("enable", help="Enable an installed plugin")
    enable_parser.add_argument("name", help="Plugin name")

    # koder plugin disable <name>
    disable_parser = plugin_subparsers.add_parser("disable", help="Disable an installed plugin")
    disable_parser.add_argument("name", help="Plugin name")

    # koder plugin validate <path>
    validate_parser = plugin_subparsers.add_parser("validate", help="Validate a plugin manifest")
    validate_parser.add_argument("path", help="Path to plugin directory")

    # koder plugin marketplace <action>
    marketplace_parser = plugin_subparsers.add_parser(
        "marketplace", aliases=["market"], help="Manage plugin marketplaces"
    )
    mkt_subparsers = marketplace_parser.add_subparsers(
        dest="marketplace_action", help="Marketplace actions"
    )
    mkt_subparsers.add_parser("list", help="List configured marketplaces")

    mkt_add = mkt_subparsers.add_parser("add", help="Add a marketplace source")
    mkt_add.add_argument(
        "source_path", help="Marketplace source: owner/repo (GitHub), git URL, or local path"
    )

    mkt_rm = mkt_subparsers.add_parser("remove", aliases=["rm"], help="Remove a marketplace")
    mkt_rm.add_argument("marketplace_name", help="Marketplace name")


def create_auth_subparsers(subparsers):
    """Create auth subcommand parsers."""
    auth_parser = subparsers.add_parser("auth", help="Manage OAuth authentication")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", help="Auth actions")

    # koder auth login <provider>
    login_parser = auth_subparsers.add_parser("login", help="Authenticate with a provider")
    login_parser.add_argument(
        "provider",
        choices=["google", "claude", "chatgpt", "antigravity"],
        help="OAuth provider (google, claude, chatgpt, antigravity)",
    )
    login_parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="Timeout in seconds for OAuth flow (default: 300)",
    )

    # koder auth list
    auth_subparsers.add_parser("list", help="List configured OAuth providers")

    # koder auth revoke <provider>
    revoke_parser = auth_subparsers.add_parser("revoke", help="Revoke OAuth tokens")
    revoke_parser.add_argument(
        "provider",
        choices=["google", "claude", "chatgpt", "antigravity"],
        help="OAuth provider to revoke",
    )

    # koder auth status [provider]
    status_parser = auth_subparsers.add_parser("status", help="Show OAuth token status")
    status_parser.add_argument(
        "provider",
        nargs="?",
        choices=["google", "claude", "chatgpt", "antigravity"],
        help="Optional: specific provider to show",
    )


def _build_runtime_request_for_test(argv: list[str]):
    from .harness.cli.entrypoint import build_runtime_request

    return build_runtime_request(argv)


def _build_cli_parser(first_arg: Optional[str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Koder - AI Coding Assistant")
    parser.add_argument("--session", "-s", default=None, help="Session ID for context")
    parser.add_argument(
        "--agents",
        default=None,
        help="JSON object defining custom agents for this invocation",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Run the main session as the named agent",
    )
    parser.add_argument(
        "--teammate-mode",
        choices=["auto", "tmux", "in-process"],
        default=None,
        help="Choose teammate display mode for agent teams",
    )
    parser.add_argument(
        "--continue",
        "-c",
        dest="continue_session",
        action="store_true",
        help="Continue the most recent session in the current directory",
    )
    parser.add_argument(
        "--resume",
        "-r",
        nargs="?",
        const=True,
        default=None,
        help="Resume a previous session by ID, or open the interactive picker when no ID is provided",
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_prompt",
        nargs="*",
        help="Print response and exit",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json", "stream-json"],
        default="text",
        help="Output format for print mode",
    )
    parser.add_argument(
        "--json-schema",
        default=None,
        help="Validate print-mode JSON output against a JSON Schema",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="Replace the entire system prompt with custom text",
    )
    parser.add_argument(
        "--system-prompt-file",
        default=None,
        help="Load system prompt from a file, replacing the default prompt",
    )
    parser.add_argument(
        "--append-system-prompt",
        default=None,
        help="Append custom text to the end of the default system prompt",
    )
    parser.add_argument(
        "--append-system-prompt-file",
        default=None,
        help="Load additional system prompt text from a file and append to the default prompt",
    )
    parser.add_argument(
        "--bare",
        action="store_true",
        help="Skip auto-discovery of hooks, skills, plugins, MCP servers, auto memory, and AGENTS.md",
    )
    parser.add_argument(
        "--allowedTools",
        dest="allowed_tools",
        action="append",
        default=[],
        help="Tools that execute without prompting for permission",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose turn-by-turn output for print mode",
    )
    parser.add_argument(
        "--include-partial-messages",
        action="store_true",
        help="Include partial message deltas in stream-json print output",
    )
    parser.add_argument(
        "--input-format",
        choices=["text", "stream-json"],
        default="text",
        help="Input format for print mode",
    )
    parser.add_argument(
        "--replay-user-messages",
        action="store_true",
        help="Re-emit stream-json user messages on stdout for acknowledgment",
    )
    parser.add_argument("--version", "-v", action="store_true", help="Show version and exit")
    parser.add_argument("--name", "-n", default=None, help="Set a display name for the session")
    parser.add_argument("--no-stream", action="store_true", help="Disable streaming mode")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--plugin-dir",
        action="append",
        default=[],
        help="Load a plugin from a directory for this session only (can be repeated)",
    )
    parser.add_argument(
        "--channels",
        type=str,
        default=None,
        help="Comma-separated channel entries to enable (e.g., server:webhook,plugin:slack@anthropic)",
    )
    parser.add_argument(
        "--dangerously-load-development-channels",
        type=str,
        default=None,
        dest="dev_channels",
        help="Comma-separated dev channel entries (bypasses allowlist for local testing)",
    )

    if first_arg in ("mcp", "config", "auth", "agents", "plugin", "plugins"):
        subparsers = parser.add_subparsers(dest="command", help="Available commands")
        create_mcp_subparsers(subparsers)
        create_config_subparsers(subparsers)
        create_agents_subparsers(subparsers)
        create_plugin_subparsers(subparsers)
        create_auth_subparsers(subparsers)
    else:
        parser.add_argument(
            "prompt", nargs="*", help="Prompt text (if not provided, starts interactive mode)"
        )

    return parser


def _append_subcommand_help(help_text: str) -> str:
    command_lines = [
        "",
        "Commands:",
        "  auth                Manage OAuth authentication",
        "                      auth <login|list|revoke|status>",
        "  mcp                 Manage MCP servers",
        "                      mcp <add|add-json|list|get|remove|reset-project-choices|serve>",
        "  config              Manage configuration",
        "                      config <show|list|path|edit|init|set>",
        "  agents              List configured agents",
        "                      agents",
        "  plugin, plugins     Manage installed plugins",
        "                      plugin <list|install|uninstall|enable|disable|validate|marketplace>",
        "",
        "Use `koder <command> --help` for subcommand details.",
    ]
    return help_text.rstrip() + "\n" + "\n".join(command_lines) + "\n"


async def main():
    """Run the Koder CLI.

    Returns:
        int: The exit code.
    """
    argv = sys.argv[1:]

    from .harness.cli.entrypoint import RuntimeRequest, build_runtime_request, run_harness_runtime

    runtime_request = build_runtime_request(argv)
    if runtime_request.mode == "help":
        parser = _build_cli_parser(runtime_request.first_arg)
        help_text = parser.format_help()
        if runtime_request.first_arg is None:
            help_text = _append_subcommand_help(help_text)
        runtime_request = RuntimeRequest(
            argv=argv,
            mode=runtime_request.mode,
            help_text=help_text,
            first_arg=runtime_request.first_arg,
        )
    return await run_harness_runtime(runtime_request)


def run():
    """Run the Koder CLI."""
    try:
        exit_code = asyncio.run(main())
        exit(exit_code)
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        # Silently exit on Ctrl+C, Ctrl+D, or task cancellation
        exit(0)
    except SystemExit:
        # Re-raise SystemExit to allow normal exit
        raise
    except Exception as e:
        console.print(
            Panel(f"[red]Fatal error: {e}[/red]", title="Fatal Error", border_style="red")
        )
        exit(1)


if __name__ == "__main__":
    run()
