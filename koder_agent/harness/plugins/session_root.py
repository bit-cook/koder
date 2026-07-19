"""Helpers for session-only plugin directory overlays."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from koder_agent.harness.paths import harness_home_dir

from .lifecycle import PluginLifecycleService
from .manifest import parse_manifest
from .path_safety import PinnedDirectory, PluginRootGuard
from .state import PluginState


def default_plugin_root() -> Path:
    return harness_home_dir() / "plugins"


def _session_name(identifier: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]", "_", identifier) or "session"
    if name in {".", ".."}:
        return "session"
    return name


def build_session_plugin_root(
    identifier: str,
    plugin_dirs: Iterable[str | Path],
    *,
    base_root: str | Path | None = None,
) -> Path:
    """Build and atomically publish a session root through a pinned parent fd."""
    base_root_path = Path(base_root).expanduser() if base_root else default_plugin_root()
    parent_guard = PluginRootGuard(harness_home_dir() / "session-plugins")
    session_name = _session_name(identifier)
    staging: PinnedDirectory = parent_guard.staging_dir("session", purpose="session")
    backup = parent_guard.staging_dir("session", purpose="backup")
    backup_name = backup.name
    backup.close()
    parent_guard.remove_entry_name(backup_name)
    published = False
    previous_moved = False

    try:
        source_lifecycle = PluginLifecycleService(base_root_path)
        session_lifecycle = PluginLifecycleService(staging.path)

        for manifest, state in source_lifecycle.installed_plugins():
            source_dir = source_lifecycle.resolve_plugin_target(manifest.name)
            result = session_lifecycle.install_from_manifest(source_dir, manifest, state=state)
            if not result.success:
                raise ValueError(
                    f"Cannot stage installed plugin '{manifest.name}': {result.message}"
                )

        for raw_path in plugin_dirs:
            plugin_dir = Path(raw_path).expanduser()
            manifest, errors, _warnings = parse_manifest(plugin_dir)
            if manifest is None or errors:
                reason = "; ".join(errors) if errors else "Invalid plugin manifest"
                raise ValueError(f"--plugin-dir invalid plugin '{plugin_dir}': {reason}")
            result = session_lifecycle.install_from_manifest(
                plugin_dir,
                manifest,
                state=PluginState(enabled=True, scope="session"),
            )
            if not result.success:
                raise ValueError(f"--plugin-dir invalid plugin '{plugin_dir}': {result.message}")

        if parent_guard.entry_exists(session_name):
            parent_guard.replace(session_name, backup_name)
            previous_moved = True
        parent_guard.replace(staging.name, session_name)
        published = True

        if previous_moved and parent_guard.entry_exists(backup_name):
            parent_guard.remove_entry_name(backup_name)
        parent_guard.verify_access_path()
        return parent_guard.root / session_name
    except Exception:
        try:
            if published and parent_guard.entry_exists(session_name):
                parent_guard.remove_entry_name(session_name)
            if previous_moved and parent_guard.entry_exists(backup_name):
                parent_guard.replace(backup_name, session_name)
        except OSError:
            pass
        raise
    finally:
        staging.close()
        if parent_guard.entry_exists(staging.name):
            parent_guard.remove_entry_name(staging.name)
