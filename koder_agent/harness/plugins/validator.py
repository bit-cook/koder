"""Trust and dependency validation for plugins."""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import PluginCatalog, PluginRecord


@dataclass(frozen=True)
class PluginTrustResult:
    requires_trust_ack: bool


@dataclass(frozen=True)
class DependencyValidationResult:
    ok: bool
    missing: tuple[str, ...] = ()


class PluginTrustService:
    """Evaluates trust requirements for plugin installs."""

    def __init__(self, catalog: PluginCatalog):
        self.catalog = catalog

    @classmethod
    def for_test(cls) -> "PluginTrustService":
        return cls(PluginCatalog.for_test())

    def evaluate(self, plugin_name: str) -> PluginTrustResult:
        plugin = self.catalog.get(plugin_name)
        return PluginTrustResult(requires_trust_ack=plugin.requires_trust_ack)


def validate_plugin_dependencies(
    plugin: PluginRecord,
    *,
    installed_plugins: set[str],
) -> DependencyValidationResult:
    missing = tuple(dep for dep in plugin.dependencies if dep not in installed_plugins)
    return DependencyValidationResult(ok=not missing, missing=missing)
