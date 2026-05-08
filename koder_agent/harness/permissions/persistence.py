"""Persistence for permission rules."""

from __future__ import annotations

import json
from pathlib import Path


class PermissionStore:
    """Atomically persists permission rules to disk."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {"rules": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)
