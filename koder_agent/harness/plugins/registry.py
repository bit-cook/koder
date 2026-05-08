"""Registry export for installed plugins."""

from __future__ import annotations

from dataclasses import dataclass, field

from .lifecycle import PluginLifecycleService


@dataclass(frozen=True)
class InstalledPlugin:
    name: str
    version: str
    scope: str = "user"
    enabled: bool = True
    description: str = ""
    components: tuple[str, ...] = field(default_factory=tuple)


class PluginRegistry:
    """Exports validated plugin descriptors from lifecycle state."""

    def __init__(self, plugins: list[InstalledPlugin]):
        self._plugins = plugins

    @classmethod
    def from_lifecycle(
        cls, lifecycle: PluginLifecycleService, *, include_disabled: bool = False
    ) -> "PluginRegistry":
        plugins: list[InstalledPlugin] = []
        for manifest, state in lifecycle.installed_plugins():
            if not include_disabled and not state.enabled:
                continue
            # Detect components
            components: list[str] = []
            plugin_dir = lifecycle.root / manifest.name
            if (plugin_dir / "skills").is_dir():
                components.append("skills")
            if (plugin_dir / "agents").is_dir():
                components.append("agents")
            if (plugin_dir / "hooks").is_dir():
                components.append("hooks")
            if (plugin_dir / ".mcp.json").is_file():
                components.append("mcp")

            plugins.append(
                InstalledPlugin(
                    name=manifest.name,
                    version=manifest.version,
                    scope=state.scope,
                    enabled=state.enabled,
                    description=manifest.description,
                    components=tuple(components),
                )
            )
        return cls(plugins)

    def list_names(self) -> list[str]:
        return [plugin.name for plugin in self._plugins]

    def list_plugins(self) -> list[InstalledPlugin]:
        return list(self._plugins)
