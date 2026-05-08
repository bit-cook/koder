"""Plugin option schema helpers."""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import PluginCatalog


@dataclass(frozen=True)
class PluginOptionSchema:
    """Resolved option defaults for a plugin."""

    plugin_name: str
    options: dict[str, object]

    @classmethod
    def for_plugin(cls, plugin_name: str) -> "PluginOptionSchema":
        plugin = PluginCatalog.for_test().get(plugin_name)
        return cls(plugin_name=plugin_name, options=dict(plugin.options))
