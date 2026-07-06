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
