"""Registry export for installed plugins."""

from __future__ import annotations

from dataclasses import dataclass, field

from .lifecycle import PluginLifecycleService
from .path_safety import PluginPathError, open_plugin_component


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
            components: list[str] = []
            plugin_dir = lifecycle.resolve_plugin_target(manifest.name)
            checks = (
                ("skills", manifest.skills, "skills", "directory"),
                ("agents", manifest.agents, "agents", "directory"),
                ("hooks", manifest.hooks, "hooks/hooks.json", "file"),
                ("mcp", manifest.mcp_servers, ".mcp.json", "file"),
            )
            for component, declared, default, expect in checks:
                try:
                    with open_plugin_component(
                        plugin_dir,
                        declared,
                        default=default,
                        field_name=component,
                        expect=expect,
                    ) as path:
                        if path is not None:
                            components.append(component)
                except PluginPathError:
                    continue

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
