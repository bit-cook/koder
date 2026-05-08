"""Plugin system primitives."""

from .catalog import PluginCatalog, PluginRecord
from .env import expand_plugin_vars, plugin_data_dir, plugin_env_vars
from .manifest import PluginManifest, find_manifest, parse_manifest
from .marketplace import MarketplaceStore
from .options import PluginOptionSchema
from .state import PluginState, PluginStateStore
from .validator import (
    DependencyValidationResult,
    PluginTrustResult,
    PluginTrustService,
    validate_plugin_dependencies,
)

__all__ = [
    "DependencyValidationResult",
    "MarketplaceStore",
    "PluginCatalog",
    "PluginManifest",
    "PluginOptionSchema",
    "PluginRecord",
    "PluginState",
    "PluginStateStore",
    "PluginTrustResult",
    "PluginTrustService",
    "expand_plugin_vars",
    "find_manifest",
    "parse_manifest",
    "plugin_data_dir",
    "plugin_env_vars",
    "validate_plugin_dependencies",
]
