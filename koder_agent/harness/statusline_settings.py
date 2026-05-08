"""Helpers for loading and updating status line settings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from koder_agent.harness.paths import harness_home_dir, settings_path


@dataclass(frozen=True)
class StatusLineConfig:
    """Resolved status line configuration from runtime settings."""

    command: str
    padding: int = 0
    source_path: Path | None = None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _iter_project_settings_paths(cwd: str | Path) -> list[Path]:
    paths: list[Path] = []
    current = Path(cwd).resolve()
    home = Path.home().resolve()
    while True:
        local = current / ".koder" / "settings.local.json"
        if local.exists():
            paths.append(local)
        project = settings_path(current)
        if project.exists():
            paths.append(project)
        if current == home or current.parent == current:
            break
        if (current / ".git").exists():
            break
        current = current.parent
    return paths


def resolve_statusline_config(cwd: str | Path) -> StatusLineConfig | None:
    """Resolve the nearest active status line config for the current cwd."""

    candidate_paths = _iter_project_settings_paths(cwd)
    candidate_paths.append(harness_home_dir() / "settings.json")
    for path in candidate_paths:
        loaded = _load_json(path)
        raw = loaded.get("statusLine")
        if not isinstance(raw, dict):
            continue
        if str(raw.get("type") or "").strip().lower() != "command":
            continue
        command = str(raw.get("command") or "").strip()
        if not command:
            continue
        padding = raw.get("padding", 0)
        try:
            padding_value = int(padding)
        except (TypeError, ValueError):
            padding_value = 0
        return StatusLineConfig(
            command=command,
            padding=max(0, padding_value),
            source_path=path,
        )
    return None


def update_user_statusline_config(config: dict[str, Any] | None) -> Path:
    """Write or remove the user-scoped status line config."""

    settings_file = harness_home_dir() / "settings.json"
    target = (
        settings_file.resolve()
        if settings_file.exists() and settings_file.is_symlink()
        else settings_file
    )
    loaded = _load_json(target) if target.exists() else {}
    if config is None:
        loaded.pop("statusLine", None)
    else:
        loaded["statusLine"] = config
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(loaded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return settings_file
