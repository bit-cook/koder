"""Plugin state persistence (enabled/disabled/scope)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class PluginState:
    """Tracks the runtime state of an installed plugin."""

    enabled: bool = True
    scope: str = "user"
    installed_at: str = ""

    def __post_init__(self) -> None:
        if not self.installed_at:
            self.installed_at = datetime.now(timezone.utc).isoformat()


class PluginStateStore:
    """Reads/writes plugin state to a JSON file."""

    def __init__(self, state_path: Path):
        self._path = state_path

    @classmethod
    def for_test(cls, root: Path) -> "PluginStateStore":
        return cls(root / "state.json")

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, name: str) -> PluginState | None:
        data = self._load()
        entry = data.get(name)
        if entry is None:
            return None
        return PluginState(
            enabled=entry.get("enabled", True),
            scope=entry.get("scope", "user"),
            installed_at=entry.get("installed_at", ""),
        )

    def set(self, name: str, state: PluginState) -> None:
        data = self._load()
        data[name] = asdict(state)
        self._save(data)

    def remove(self, name: str) -> bool:
        data = self._load()
        if name not in data:
            return False
        del data[name]
        self._save(data)
        return True

    def is_enabled(self, name: str) -> bool:
        state = self.get(name)
        if state is None:
            return True  # default: enabled if no state record
        return state.enabled

    def list_all(self) -> dict[str, PluginState]:
        data = self._load()
        result: dict[str, PluginState] = {}
        for name, entry in data.items():
            result[name] = PluginState(
                enabled=entry.get("enabled", True),
                scope=entry.get("scope", "user"),
                installed_at=entry.get("installed_at", ""),
            )
        return result
