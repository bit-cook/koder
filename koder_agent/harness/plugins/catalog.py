"""In-memory plugin catalog for runtime marketplace tests."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PluginRecord:
    """A single plugin catalog entry."""

    name: str
    version: str
    requires_trust_ack: bool
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    options: dict[str, object] = field(default_factory=dict)


class PluginCatalog:
    """Simple catalog service for plugin metadata."""

    def __init__(self, plugins: dict[str, PluginRecord]):
        self._plugins = plugins

    @classmethod
    def for_test(cls) -> "PluginCatalog":
        plugins = {
            "sample-trusted-plugin": PluginRecord(
                name="sample-trusted-plugin",
                version="1.0.0",
                requires_trust_ack=False,
                options={"enabled": True},
            ),
            "sample-untrusted-plugin": PluginRecord(
                name="sample-untrusted-plugin",
                version="1.0.0",
                requires_trust_ack=True,
                options={"enabled": True},
            ),
            "sample-dependent-plugin": PluginRecord(
                name="sample-dependent-plugin",
                version="1.0.0",
                requires_trust_ack=False,
                dependencies=("sample-trusted-plugin",),
                options={"enabled": True},
            ),
        }
        return cls(plugins)

    def get(self, name: str) -> PluginRecord:
        return self._plugins[name]

    def list_plugins(self) -> list[PluginRecord]:
        return list(self._plugins.values())
