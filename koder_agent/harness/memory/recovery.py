"""Recovery helpers for runtime transcript persistence."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RecoveryResult:
    """Result of a transcript recovery attempt."""

    recovered: bool
    reason: str


@dataclass(frozen=True)
class BackupResult:
    """Result of a transcript backup attempt."""

    created: bool
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


def default_backup_path(runtime_db_path: str | Path) -> Path:
    """Return the conventional ``<db>.bak`` path used by recovery."""
    runtime_db = Path(runtime_db_path)
    return runtime_db.with_suffix(runtime_db.suffix + ".bak")


def create_backup(
    runtime_db_path: str | Path,
    backup_db_path: str | Path | None = None,
) -> BackupResult:
    """Snapshot a HEALTHY runtime DB to ``<db>.bak`` so recovery has something to restore.

    ``recover_partial_write`` restores from a ``.bak`` sibling, but nothing was
    ever creating it. Callers that own the runtime DB writes (e.g. the transcript
    store) should invoke this at a safe point -- ideally before a risky write, and
    after a clean startup -- so a later corrupted primary can be recovered.

    Uses SQLite's online backup API to capture a transactionally-consistent copy
    even under WAL mode / concurrent access, writes it to a temp file, then
    atomically ``os.replace``s it into place. A backup is only taken when the
    source is healthy, so a corrupt DB can never clobber a good backup.

    NOTE (wiring): this helper is not yet called from the transcript store
    (``harness/memory/transcript_store.py``), which owns the actual writes and is
    outside this change's file set. That store should call ``create_backup`` after
    a successful commit so ``recover_partial_write`` has a backup to fall back to.
    """
    runtime_db = Path(runtime_db_path)
    backup_db = (
        Path(backup_db_path) if backup_db_path is not None else default_backup_path(runtime_db)
    )

    if not runtime_db.exists():
        return BackupResult(created=False, reason="no runtime database to back up")

    if not _sqlite_db_is_healthy(runtime_db):
        # Never overwrite a known-good backup with a corrupt source.
        return BackupResult(created=False, reason="runtime database is not healthy")

    backup_db.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = backup_db.with_suffix(backup_db.suffix + ".tmp")
    try:
        source = sqlite3.connect(runtime_db)
        try:
            dest = sqlite3.connect(tmp_path)
            try:
                source.backup(dest)
            finally:
                dest.close()
        finally:
            source.close()
        os.replace(tmp_path, backup_db)
    except (sqlite3.DatabaseError, OSError):
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return BackupResult(created=False, reason="failed to write backup")

    if not _sqlite_db_is_healthy(backup_db):
        return BackupResult(created=False, reason="backup failed integrity check")
    return BackupResult(created=True, reason="backup created")


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
