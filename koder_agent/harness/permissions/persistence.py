"""Persistence for permission rules."""

from __future__ import annotations

import json
from pathlib import Path


class PermissionStore:
    """Atomically persists permission rules to disk.

    The on-disk shape is ``{"rules": {tool: {behavior: [rule, ...]}}}``. Rules
    persisted here — including derived prefix rules such as ``npm test:*`` and
    per-directory ``/proj/src/`` rules — survive across sessions, so an
    "always allow" decision made in one run is honored in the next.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {"rules": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt or unreadable store must not crash startup; treat it as
            # empty so the session falls back to prompting rather than failing.
            return {"rules": {}}
        if not isinstance(data, dict):
            return {"rules": {}}
        rules = data.get("rules")
        if not isinstance(rules, dict):
            data["rules"] = {}
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)
