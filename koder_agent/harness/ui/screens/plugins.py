"""Plugin marketplace screen helpers."""

from __future__ import annotations

from koder_agent.harness.plugins.registry import PluginRegistry


def render_plugin_screen(registry: PluginRegistry) -> dict:
    """Render a minimal plugin marketplace screen model."""
    return {
        "screen": "plugins",
        "plugins": registry.list_names(),
    }
