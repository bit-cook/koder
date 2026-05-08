"""Local managed settings policy helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from koder_agent.harness.paths import harness_home_dir


@dataclass(frozen=True)
class ManagedSettingsState:
    """Resolved state for the local managed settings file."""

    path: Path
    exists: bool
    valid: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    checksum: str | None = None


def managed_settings_path() -> Path:
    return harness_home_dir() / "managed-settings.json"


def load_managed_settings(path: Path | None = None) -> ManagedSettingsState:
    """Load the local managed settings policy file if present."""
    target = path or managed_settings_path()
    if not target.exists():
        return ManagedSettingsState(path=target, exists=False, valid=True)
    try:
        content = target.read_text(encoding="utf-8")
        loaded = json.loads(content)
    except Exception as exc:
        return ManagedSettingsState(path=target, exists=True, valid=False, error=str(exc))
    if not isinstance(loaded, dict):
        return ManagedSettingsState(
            path=target,
            exists=True,
            valid=False,
            error="managed settings root must be a JSON object",
        )
    return ManagedSettingsState(
        path=target,
        exists=True,
        valid=True,
        data=loaded,
        checksum="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def render_managed_settings_status(path: Path | None = None) -> str:
    """Render a concise status view for the local managed settings policy."""
    state = load_managed_settings(path)
    lines = ["managed_settings:"]
    lines.append(f"path: {state.path}")
    lines.append(f"exists: {str(state.exists).lower()}")
    lines.append(f"valid: {str(state.valid).lower()}")
    if state.error:
        lines.append(f"error: {state.error}")
        return "\n".join(lines)
    if not state.exists:
        lines.append("source: local file not configured")
        return "\n".join(lines)
    if state.checksum:
        lines.append(f"checksum: {state.checksum}")
    lines.append(f"disable_all_hooks: {str(state.data.get('disableAllHooks') is True).lower()}")

    hooks = state.data.get("hooks")
    hooks_events = len(hooks) if isinstance(hooks, dict) else 0
    hooks_groups = 0
    if isinstance(hooks, dict):
        hooks_groups = sum(len(groups) for groups in hooks.values() if isinstance(groups, list))
    lines.append(f"hooks_events: {hooks_events}")
    lines.append(f"hooks_groups: {hooks_groups}")

    sandbox = state.data.get("sandbox")
    sandbox_keys = sorted(sandbox) if isinstance(sandbox, dict) else []
    policy_locked = bool(
        isinstance(sandbox, dict)
        and any(
            key in sandbox
            for key in (
                "enabled",
                "autoAllowBashIfSandboxed",
                "allowUnsandboxedCommands",
            )
        )
    )
    lines.append(f"sandbox_policy_locked: {str(policy_locked).lower()}")
    lines.append("sandbox_keys: " + (", ".join(sandbox_keys) if sandbox_keys else "none"))
    lines.append("source: local managed policy file")
    return "\n".join(lines)
