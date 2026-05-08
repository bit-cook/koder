"""Read-only access helpers for the preserved legacy Koder database."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class LegacyDB:
    """Read-only handle for the preserved legacy `~/.koder/koder.db` database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.is_read_only = True

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def _connect(self) -> sqlite3.Connection:
        if not self.exists:
            raise FileNotFoundError(self.path)
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def list_tables(self) -> list[str]:
        if not self.exists:
            return []
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()
