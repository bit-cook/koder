"""Project-scoped MCP server approval state."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from filelock import FileLock

_APPROVALS_VERSION = 3
_PROCESS_LOCK = threading.RLock()


def _approvals_path() -> Path:
    return Path.home() / ".koder" / "mcp-project-approvals.json"


def _empty_approvals() -> dict[str, Any]:
    return {"version": _APPROVALS_VERSION, "projects": {}, "legacy": {}}


@contextmanager
def _approvals_lock() -> Generator[None, None, None]:
    """Serialize read-modify-write operations across threads and platforms."""
    with _PROCESS_LOCK:
        path = _approvals_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(path.with_suffix(path.suffix + ".lock")), timeout=30)
        with lock:
            yield


def _load_approvals_unlocked() -> dict[str, Any]:
    path = _approvals_path()
    if not path.exists():
        return _empty_approvals()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_approvals()

    if not isinstance(raw, dict):
        return _empty_approvals()
    if raw.get("version") == _APPROVALS_VERSION and isinstance(raw.get("projects"), dict):
        return {
            "version": _APPROVALS_VERSION,
            "projects": raw["projects"],
            "legacy": raw.get("legacy", {}) if isinstance(raw.get("legacy"), dict) else {},
        }

    # Older formats did not bind approval to project root + source + expanded
    # digest. Preserve their records only so reset remains useful; they never
    # authorize a connection.
    legacy: dict[str, Any] = {}
    if raw.get("version") == 2:
        for source, entry in (raw.get("sources") or {}).items():
            if isinstance(entry, dict):
                legacy[str(source)] = entry.get("approved")
        old_legacy = raw.get("legacy")
        if isinstance(old_legacy, dict):
            legacy.update(old_legacy)
    else:
        legacy = raw
    return {"version": _APPROVALS_VERSION, "projects": {}, "legacy": legacy}


def _save_approvals_unlocked(approvals: dict[str, Any]) -> None:
    path = _approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(approvals, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(payload)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:  # pragma: no cover - platform/filesystem dependent
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _canonical(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def is_project_approved(project_root: str | Path) -> bool | None:
    """Return a legacy path-only decision without authorizing a connection."""
    with _approvals_lock():
        approvals = _load_approvals_unlocked()
    decision = approvals["legacy"].get(_canonical(project_root))
    return decision if isinstance(decision, bool) else None


def is_project_source_approved(
    *,
    project_root: str | Path,
    source_path: str | Path,
    source_digest: str,
) -> bool | None:
    """Return the decision bound to one root, source, and expanded digest."""
    with _approvals_lock():
        approvals = _load_approvals_unlocked()
    project = approvals["projects"].get(_canonical(project_root))
    if not isinstance(project, dict):
        return None
    sources = project.get("sources")
    if not isinstance(sources, dict):
        return None
    entry = sources.get(_canonical(source_path))
    if not isinstance(entry, dict) or entry.get("digest") != source_digest:
        return None
    decision = entry.get("approved")
    return decision if isinstance(decision, bool) else None


def set_project_approval(
    *,
    project_root: str | Path,
    source_path: str | Path,
    source_digest: str,
    approved: bool,
) -> None:
    """Store an approval bound to root + source + expanded executable digest."""
    with _approvals_lock():
        approvals = _load_approvals_unlocked()
        root_key = _canonical(project_root)
        if not isinstance(source_path, (str, Path)):
            raise TypeError("source_path is required for project MCP approval")
        if not isinstance(source_digest, str) or not source_digest:
            raise ValueError("source_digest is required for project MCP approval")
        if not isinstance(approved, bool):
            raise TypeError("approved must be a boolean")

        project = approvals["projects"].setdefault(root_key, {"sources": {}})
        sources = project.setdefault("sources", {})
        sources[_canonical(source_path)] = {
            "approved": approved,
            "digest": source_digest,
        }
        _save_approvals_unlocked(approvals)


def is_project_connect_allowed(
    *,
    project_root: str | Path,
    source_path: str | Path,
    source_digest: str,
) -> bool:
    """Return whether this exact project MCP executable configuration may run."""
    with _approvals_lock():
        return _is_project_connect_allowed_unlocked(
            project_root=project_root,
            source_path=source_path,
            source_digest=source_digest,
        )


def _is_project_connect_allowed_unlocked(
    *,
    project_root: str | Path,
    source_path: str | Path,
    source_digest: str,
) -> bool:
    """Check one approval while the caller holds ``_approvals_lock``.

    Runtime operation admission uses this internal form so the approval read
    and the in-process in-flight admission record share one linearization
    boundary with cross-process reset writers.
    """
    approvals = _load_approvals_unlocked()
    project = approvals["projects"].get(_canonical(project_root))
    if not isinstance(project, dict):
        return False
    sources = project.get("sources")
    if not isinstance(sources, dict):
        return False
    entry = sources.get(_canonical(source_path))
    return bool(
        isinstance(entry, dict)
        and entry.get("digest") == source_digest
        and entry.get("approved") is True
    )


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def reset_project_choices(project_root: str | Path | None = None) -> int:
    """Atomically reset stored project approval decisions."""
    with _approvals_lock():
        approvals = _load_approvals_unlocked()
        projects = approvals["projects"]
        legacy = approvals["legacy"]
        if not projects and not legacy:
            return 0

        if project_root is None:
            count = sum(
                len(project.get("sources", {}))
                for project in projects.values()
                if isinstance(project, dict)
            ) + len(legacy)
            _save_approvals_unlocked(_empty_approvals())
            return count

        root = Path(project_root).expanduser().resolve()
        root_key = str(root)
        count = 0
        project = projects.pop(root_key, None)
        if isinstance(project, dict):
            sources = project.get("sources")
            if isinstance(sources, dict):
                count += len(sources)

        legacy_keys = [
            key for key in legacy if _path_is_within(Path(key).expanduser().resolve(), root)
        ]
        for key in legacy_keys:
            del legacy[key]
        count += len(legacy_keys)
        if count:
            _save_approvals_unlocked(approvals)
        return count
