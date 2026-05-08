"""Plugin environment variable helpers."""

from __future__ import annotations

import re
from pathlib import Path

from koder_agent.harness.paths import harness_home_dir


def plugin_data_dir(plugin_name: str) -> Path:
    """Return the persistent data directory for a plugin.

    Path: ~/.koder/plugin-data/<plugin-name>/
    This directory survives plugin updates and is only removed on uninstall.
    """
    sanitised = re.sub(r"[^a-zA-Z0-9]", "-", plugin_name)
    return harness_home_dir() / "plugin-data" / sanitised


def plugin_env_vars(plugin_name: str, plugin_dir: Path) -> dict[str, str]:
    """Return environment variables for a plugin's execution context.

    Sets Koder-owned plugin paths only.
    """
    root = str(plugin_dir.resolve())
    data = str(plugin_data_dir(plugin_name))
    return {
        "KODER_PLUGIN_ROOT": root,
        "KODER_PLUGIN_DATA": data,
    }


def expand_plugin_vars(text: str, plugin_name: str, plugin_dir: Path) -> str:
    """Expand Koder plugin variables in text."""
    env = plugin_env_vars(plugin_name, plugin_dir)
    for key, value in env.items():
        text = text.replace(f"${{{key}}}", value)
    return text
