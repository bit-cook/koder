"""Harness CLI handlers for agent commands."""

from __future__ import annotations

import json
from pathlib import Path

from koder_agent.harness.plugins.session_root import build_session_plugin_root, default_plugin_root

from .definitions import get_agent_definitions, render_agent_profiles


async def handle_agents_subcommand(_args) -> int:
    cli_agents_json = None
    raw_agents = getattr(_args, "agents", None)
    if raw_agents:
        cli_agents_json = json.loads(raw_agents)
    plugin_dirs = list(getattr(_args, "plugin_dir", []) or [])
    plugin_root = default_plugin_root()
    if plugin_dirs:
        plugin_root = build_session_plugin_root("agents-subcommand", plugin_dirs)
    definitions = get_agent_definitions(
        cwd=Path.cwd(),
        plugin_root=plugin_root,
        cli_agents_json=cli_agents_json,
    )
    print(render_agent_profiles(definitions.active_agents))
    return 0
