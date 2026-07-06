"""On-disk file checkpointing for /rewind code restoration.

Provides file-history snapshots for ``/rewind`` code restoration: before each
mutating file operation (``write_file`` / ``edit_file`` / ``append_file``) the
*pre-edit* content of the target file is snapshotted to an on-disk backup store
keyed by session and a monotonically increasing turn/checkpoint counter.

``/rewind`` can then restore tracked files to a chosen point in the
conversation ("code" restore) in addition to (or instead of) trimming the
conversation history ("conversation" restore).

Design notes
------------
* No git is used. Snapshots live under ``~/.koder/checkpoints/<session>/``.
* File tools are stateless module-level functions, so this module holds the
  "active session" and a monotonic checkpoint counter that the tools bump and
  that ``/rewind`` reads (see ``record_pre_edit`` / ``restore_to``).
* File checkpointing is gated behind a config flag that defaults **ON**. It can
  be disabled by setting ``KODER_FILE_CHECKPOINTS`` to a falsy value
  (``0``/``false``/``no``/``off``).
* Each session's snapshot store is bounded to ``MAX_SNAPSHOTS`` most recent
  entries so the on-disk store cannot grow without bound.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Environment flag gating file checkpointing. Defaults ON.
CHECKPOINT_ENV = "KODER_FILE_CHECKPOINTS"

# Maximum number of individual file snapshots retained per session. Oldest
# snapshots are pruned once this bound is exceeded.
MAX_SNAPSHOTS = 200

_FALSY = {"0", "false", "no", "off", "disable", "disabled"}

# Sentinel written into the manifest to mark that the file did not exist at
# snapshot time (a "tombstone"). Restoring to before this checkpoint must
# delete the file.
_TOMBSTONE = "__KODER_CHECKPOINT_TOMBSTONE__"


def checkpoints_enabled() -> bool:
    """Return whether file checkpointing is enabled (default ON)."""
    raw = os.environ.get(CHECKPOINT_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY


def _checkpoints_root() -> Path:
    """Root directory for all checkpoint stores (overridable for tests)."""
    override = os.environ.get("KODER_CHECKPOINT_DIR")
    if override:
        return Path(override)
    return Path.home() / ".koder" / "checkpoints"


def _safe_session_dir(session_id: str) -> str:
    """Return a filesystem-safe directory name for a session id."""
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in session_id)
    return safe or "default"


@dataclass
class _SnapshotRecord:
    """A single pre-edit snapshot entry stored in the manifest."""

    checkpoint: int  # monotonic counter at snapshot time
    path: str  # absolute path of the file that was about to change
    blob: str  # relative filename of the backup blob, or _TOMBSTONE
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "checkpoint": self.checkpoint,
            "path": self.path,
            "blob": self.blob,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "_SnapshotRecord":
        return cls(
            checkpoint=int(data["checkpoint"]),
            path=str(data["path"]),
            blob=str(data["blob"]),
            timestamp=float(data.get("timestamp", 0.0)),
        )


class CheckpointStore:
    """On-disk pre-edit snapshot store for a single session.

    Layout::

        <root>/<session>/manifest.json      # list of snapshot records
        <root>/<session>/blobs/<n>.bak       # backup file contents

    The store is intentionally simple: append-only within a session, pruned to
    the most recent ``MAX_SNAPSHOTS`` entries.
    """

    def __init__(self, session_id: str, *, root: Optional[Path] = None) -> None:
        self.session_id = session_id
        base = root if root is not None else _checkpoints_root()
        self.dir = Path(base) / _safe_session_dir(session_id)
        self.blobs_dir = self.dir / "blobs"
        self.manifest_path = self.dir / "manifest.json"
        self._lock = threading.Lock()

    # -- persistence -------------------------------------------------------
    def _load(self) -> List[_SnapshotRecord]:
        if not self.manifest_path.exists():
            return []
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.debug("Failed to read checkpoint manifest", exc_info=True)
            return []
        records: List[_SnapshotRecord] = []
        if isinstance(raw, list):
            for entry in raw:
                try:
                    records.append(_SnapshotRecord.from_dict(entry))
                except (KeyError, TypeError, ValueError):
                    continue
        return records

    def _save(self, records: List[_SnapshotRecord]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps([r.to_dict() for r in records], indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.manifest_path)

    def _prune(self, records: List[_SnapshotRecord]) -> List[_SnapshotRecord]:
        """Drop oldest snapshots (and their blobs) beyond ``MAX_SNAPSHOTS``."""
        if len(records) <= MAX_SNAPSHOTS:
            return records
        excess = len(records) - MAX_SNAPSHOTS
        dropped = records[:excess]
        for rec in dropped:
            if rec.blob != _TOMBSTONE:
                try:
                    (self.blobs_dir / rec.blob).unlink(missing_ok=True)
                except OSError:
                    logger.debug("Failed to prune blob %s", rec.blob, exc_info=True)
        return records[excess:]

    # -- public API --------------------------------------------------------
    def record(self, path: str, checkpoint: int) -> None:
        """Snapshot the current on-disk content of ``path`` at ``checkpoint``.

        If the file does not exist a tombstone is recorded so a later restore
        deletes it. Multiple edits to the same file within the same checkpoint
        keep only the *first* (earliest, i.e. true pre-edit) snapshot, so a
        restore returns the file to how it looked before this checkpoint.
        """
        with self._lock:
            records = self._load()

            # Preserve the earliest snapshot of this path at this checkpoint.
            for rec in records:
                if rec.checkpoint == checkpoint and rec.path == path:
                    return

            abs_path = str(Path(path).resolve())
            src = Path(abs_path)
            self.blobs_dir.mkdir(parents=True, exist_ok=True)

            if src.exists() and src.is_file():
                blob_name = f"{int(time.time() * 1_000_000)}_{len(records)}.bak"
                try:
                    shutil.copy2(src, self.blobs_dir / blob_name)
                except OSError:
                    logger.debug("Failed to snapshot %s", abs_path, exc_info=True)
                    return
                blob = blob_name
            else:
                blob = _TOMBSTONE

            records.append(
                _SnapshotRecord(
                    checkpoint=checkpoint,
                    path=abs_path,
                    blob=blob,
                    timestamp=time.time(),
                )
            )
            records = self._prune(records)
            self._save(records)

    def restore_to(self, checkpoint: int) -> List[str]:
        """Restore all files snapshotted *after* ``checkpoint``.

        For each affected file we restore the *earliest* snapshot taken after
        ``checkpoint`` (i.e. its content as of that point in the conversation).
        Tombstones cause the file to be deleted. Snapshots at checkpoints
        strictly greater than ``checkpoint`` are consumed (removed) so a
        subsequent restore is idempotent.

        Returns the list of absolute file paths that were restored/removed.
        """
        with self._lock:
            records = self._load()
            # Snapshots taken after the target checkpoint, oldest first.
            affected = [r for r in records if r.checkpoint > checkpoint]
            if not affected:
                return []

            # Earliest snapshot per path == pre-edit content just after target.
            earliest: Dict[str, _SnapshotRecord] = {}
            for rec in affected:
                if rec.path not in earliest:
                    earliest[rec.path] = rec

            restored: List[str] = []
            for path, rec in earliest.items():
                target = Path(path)
                try:
                    if rec.blob == _TOMBSTONE:
                        if target.exists():
                            target.unlink()
                        restored.append(path)
                    else:
                        blob_path = self.blobs_dir / rec.blob
                        if blob_path.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(blob_path, target)
                            restored.append(path)
                except OSError:
                    logger.debug("Failed to restore %s", path, exc_info=True)

            # Consume the restored checkpoints and prune their blobs.
            remaining = [r for r in records if r.checkpoint <= checkpoint]
            for rec in affected:
                if rec.blob != _TOMBSTONE:
                    try:
                        (self.blobs_dir / rec.blob).unlink(missing_ok=True)
                    except OSError:
                        pass
            self._save(remaining)
            return restored

    def tracked_paths_after(self, checkpoint: int) -> List[str]:
        """Return distinct file paths with snapshots after ``checkpoint``."""
        with self._lock:
            records = self._load()
        seen: List[str] = []
        for rec in records:
            if rec.checkpoint > checkpoint and rec.path not in seen:
                seen.append(rec.path)
        return seen

    def clear(self) -> None:
        """Remove the entire snapshot store for this session."""
        with self._lock:
            if self.dir.exists():
                shutil.rmtree(self.dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Module-level active-session + checkpoint counter.
#
# File tools are stateless functions; they call ``record_pre_edit`` which uses
# the active session and the current monotonic counter. ``/rewind`` reads the
# counter and calls ``restore_to``.
# ---------------------------------------------------------------------------
_state_lock = threading.Lock()
_active_session_id: Optional[str] = None
_checkpoint_counter: int = 0
_store_cache: Dict[str, CheckpointStore] = {}


def set_active_session(session_id: Optional[str]) -> None:
    """Bind the active session for subsequent file-tool checkpoints."""
    global _active_session_id, _checkpoint_counter
    with _state_lock:
        if session_id != _active_session_id:
            _checkpoint_counter = 0
        _active_session_id = session_id


def get_active_session() -> Optional[str]:
    with _state_lock:
        return _active_session_id


def current_checkpoint() -> int:
    """Return the current monotonic checkpoint counter."""
    with _state_lock:
        return _checkpoint_counter


def begin_turn() -> int:
    """Advance to a new checkpoint (called at the start of a user turn).

    Returns the new checkpoint value. File edits recorded after this call are
    associated with the returned checkpoint.
    """
    global _checkpoint_counter
    with _state_lock:
        _checkpoint_counter += 1
        return _checkpoint_counter


def _get_store(session_id: str) -> CheckpointStore:
    with _state_lock:
        store = _store_cache.get(session_id)
        if store is None:
            store = CheckpointStore(session_id)
            _store_cache[session_id] = store
        return store


def record_pre_edit(path: str, session_id: Optional[str] = None) -> None:
    """Snapshot ``path``'s pre-edit content for the active session/turn.

    Safe to call unconditionally from file tools: it is a no-op when
    checkpointing is disabled or no active session is bound.
    """
    if not checkpoints_enabled():
        return
    sid = session_id if session_id is not None else get_active_session()
    if not sid:
        return
    try:
        store = _get_store(sid)
        # Ensure at least one checkpoint exists so restore has a target range.
        with _state_lock:
            if _checkpoint_counter == 0:
                globals()["_checkpoint_counter"] = 1
            checkpoint = _checkpoint_counter
        store.record(path, checkpoint)
    except Exception:  # defensive: never break a file op over checkpointing
        logger.debug("record_pre_edit failed for %s", path, exc_info=True)


def restore_to(session_id: str, checkpoint: int) -> List[str]:
    """Restore all tracked files changed after ``checkpoint`` for a session."""
    store = _get_store(session_id)
    return store.restore_to(checkpoint)


def tracked_paths_after(session_id: str, checkpoint: int) -> List[str]:
    store = _get_store(session_id)
    return store.tracked_paths_after(checkpoint)


def reset_state_for_tests() -> None:
    """Testing helper: clear module-level active-session state and cache."""
    global _active_session_id, _checkpoint_counter, _store_cache
    with _state_lock:
        _active_session_id = None
        _checkpoint_counter = 0
        _store_cache = {}
