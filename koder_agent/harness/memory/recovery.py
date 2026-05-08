"""Recovery helpers for runtime transcript persistence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RecoveryResult:
    """Result of a transcript recovery attempt."""

    recovered: bool
    reason: str


def _sqlite_db_is_healthy(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(result and result[0] == "ok")
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def recover_partial_write(
    runtime_db_path: str | Path,
    backup_db_path: str | Path | None = None,
) -> RecoveryResult:
    """Restore a runtime transcript DB from the last known good backup when needed."""
    runtime_db = Path(runtime_db_path)
    backup_db = (
        Path(backup_db_path)
        if backup_db_path is not None
        else runtime_db.with_suffix(runtime_db.suffix + ".bak")
    )

    if _sqlite_db_is_healthy(runtime_db):
        return RecoveryResult(recovered=False, reason="primary database is healthy")

    if not backup_db.exists():
        return RecoveryResult(recovered=False, reason="no backup database available")

    if not _sqlite_db_is_healthy(backup_db):
        return RecoveryResult(recovered=False, reason="backup database is not healthy")

    runtime_db.parent.mkdir(parents=True, exist_ok=True)
    runtime_db.write_bytes(backup_db.read_bytes())
    if _sqlite_db_is_healthy(runtime_db):
        return RecoveryResult(recovered=True, reason="restored from backup")
    return RecoveryResult(recovered=False, reason="restored database failed integrity check")
