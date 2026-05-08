"""Helpers for session-only plugin directory overlays."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Iterable

from koder_agent.harness.paths import harness_home_dir

from .lifecycle import PluginLifecycleService
from .manifest import parse_manifest
from .state import PluginState, PluginStateStore


def default_plugin_root() -> Path:
    return (harness_home_dir() / "plugins").resolve()


def build_session_plugin_root(
    identifier: str,
    plugin_dirs: Iterable[str | Path],
    *,
    base_root: str | Path | None = None,
) -> Path:
    """Build a session-scoped plugin root from installed + ad-hoc plugin dirs."""
    base_root_path = Path(base_root).expanduser().resolve() if base_root else default_plugin_root()
    session_name = re.sub(r"[^A-Za-z0-9._-]", "_", identifier) or "session"
    session_root = harness_home_dir() / "session-plugins" / session_name

    if session_root.exists():
        shutil.rmtree(session_root)
    session_root.mkdir(parents=True, exist_ok=True)

    state_store = PluginStateStore(session_root / "state.json")
    lifecycle = PluginLifecycleService(base_root_path)
    for manifest, state in lifecycle.installed_plugins():
        shutil.copytree(base_root_path / manifest.name, session_root / manifest.name)
        state_store.set(manifest.name, state)

    for plugin_dir in (Path(path).expanduser().resolve() for path in plugin_dirs):
        if not plugin_dir.is_dir():
            raise ValueError(f"--plugin-dir path is not a directory: {plugin_dir}")
        manifest, errors, _warnings = parse_manifest(plugin_dir)
        if manifest is None or errors:
            reason = "; ".join(errors) if errors else "Invalid plugin manifest"
            raise ValueError(f"--plugin-dir invalid plugin '{plugin_dir}': {reason}")
        target_dir = session_root / manifest.name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(plugin_dir, target_dir)
        state_store.set(manifest.name, PluginState(enabled=True, scope="session"))

    return session_root.resolve()
