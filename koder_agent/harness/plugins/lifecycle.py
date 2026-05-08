"""Local plugin lifecycle service with rollback support."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .manifest import PluginManifest, find_manifest, parse_manifest
from .state import PluginState, PluginStateStore


@dataclass(frozen=True)
class PluginLifecycleResult:
    success: bool
    rollback_performed: bool
    plugin_name: str | None = None
    message: str = ""


class PluginLifecycleService:
    """Installs plugins from local directories into a managed root."""

    def __init__(self, root: Path, *, state_store: PluginStateStore | None = None):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._state = state_store or PluginStateStore(root / "state.json")

    @classmethod
    def for_test(cls, root: Path) -> "PluginLifecycleService":
        base = root / "installed_plugins"
        return cls(base)

    @property
    def state_store(self) -> PluginStateStore:
        return self._state

    def install_from_dir(self, plugin_dir: Path, *, scope: str = "user") -> PluginLifecycleResult:
        """Install a plugin from a local directory."""
        manifest, errors, _warnings = parse_manifest(plugin_dir)
        if manifest is None or errors:
            return PluginLifecycleResult(
                success=False,
                rollback_performed=False,
                message="; ".join(errors) if errors else "Invalid manifest",
            )

        target_dir = self.root / manifest.name
        try:
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(plugin_dir, target_dir)
            self._state.set(manifest.name, PluginState(enabled=True, scope=scope))
            return PluginLifecycleResult(
                success=True,
                rollback_performed=False,
                plugin_name=manifest.name,
                message=f"Installed {manifest.name}@{manifest.version}",
            )
        except Exception as exc:
            if target_dir.exists():
                shutil.rmtree(target_dir)
            return PluginLifecycleResult(
                success=False,
                rollback_performed=True,
                message=str(exc),
            )

    def uninstall(self, plugin_name: str) -> PluginLifecycleResult:
        """Uninstall a plugin by name."""
        target_dir = self.root / plugin_name
        if not target_dir.exists():
            return PluginLifecycleResult(
                success=False,
                rollback_performed=False,
                plugin_name=plugin_name,
                message=f"Plugin '{plugin_name}' is not installed",
            )
        try:
            shutil.rmtree(target_dir)
            self._state.remove(plugin_name)
            return PluginLifecycleResult(
                success=True,
                rollback_performed=False,
                plugin_name=plugin_name,
                message=f"Uninstalled {plugin_name}",
            )
        except Exception as exc:
            return PluginLifecycleResult(
                success=False,
                rollback_performed=False,
                plugin_name=plugin_name,
                message=str(exc),
            )

    def enable(self, plugin_name: str) -> PluginLifecycleResult:
        """Enable an installed plugin."""
        target_dir = self.root / plugin_name
        if not target_dir.exists():
            return PluginLifecycleResult(
                success=False,
                rollback_performed=False,
                plugin_name=plugin_name,
                message=f"Plugin '{plugin_name}' is not installed",
            )
        state = self._state.get(plugin_name) or PluginState()
        state.enabled = True
        self._state.set(plugin_name, state)
        return PluginLifecycleResult(
            success=True,
            rollback_performed=False,
            plugin_name=plugin_name,
            message=f"Enabled {plugin_name}",
        )

    def disable(self, plugin_name: str) -> PluginLifecycleResult:
        """Disable an installed plugin."""
        target_dir = self.root / plugin_name
        if not target_dir.exists():
            return PluginLifecycleResult(
                success=False,
                rollback_performed=False,
                plugin_name=plugin_name,
                message=f"Plugin '{plugin_name}' is not installed",
            )
        state = self._state.get(plugin_name) or PluginState()
        state.enabled = False
        self._state.set(plugin_name, state)
        return PluginLifecycleResult(
            success=True,
            rollback_performed=False,
            plugin_name=plugin_name,
            message=f"Disabled {plugin_name}",
        )

    def is_enabled(self, plugin_name: str) -> bool:
        """Check if a plugin is enabled."""
        return self._state.is_enabled(plugin_name)

    def installed_manifests(self) -> list[dict]:
        """List raw manifests for all installed plugins."""
        manifests: list[dict] = []
        if not self.root.exists():
            return manifests
        for plugin_dir in sorted(self.root.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest_path = find_manifest(plugin_dir)
            if manifest_path is None:
                continue
            try:
                manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return manifests

    def installed_plugins(self) -> list[tuple[PluginManifest, PluginState]]:
        """List all installed plugins with their state."""
        result: list[tuple[PluginManifest, PluginState]] = []
        if not self.root.exists():
            return result
        for plugin_dir in sorted(self.root.iterdir()):
            if not plugin_dir.is_dir():
                continue
            manifest, errors, _ = parse_manifest(plugin_dir)
            if manifest is None or errors:
                continue
            state = self._state.get(manifest.name) or PluginState()
            result.append((manifest, state))
        return result
