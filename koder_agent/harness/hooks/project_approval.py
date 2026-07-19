"""Digest-bound project hook approval state.

Project hooks execute repository-controlled commands and requests. Approval is
therefore bound to both the canonical project path and the exact executable
hook payload that was reviewed, rather than trusting a path indefinitely.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

_APPROVALS_FILE = "hook-project-approvals.json"
_APPROVAL_SCHEMA_VERSION = 2
_PAYLOAD_SCHEMA_VERSION = 1
_DIGEST_ALGORITHM = "sha256"
_PROJECT_SOURCE = "project_settings"
_LOCAL_SOURCE = "local_settings"
_LOCK_TIMEOUT_SECONDS = 10
_LOCK_RETRY_SECONDS = 0.05
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# Only fields consumed by hook dispatch belong in the trust payload. Unknown
# and presentation-only settings are intentionally excluded so harmless edits
# do not force reapproval.
_EXECUTABLE_HOOK_FIELDS = (
    "type",
    "command",
    "url",
    "prompt",
    "timeout",
    "shell",
    "headers",
    "allowedEnvVars",
    "async",
    "once",
    "if",
    "model",
    "passFullEnv",
)

# In-process cache: avoids re-reading + JSON-parsing the approvals file on
# every hook dispatch. The nanosecond mtime and size detect quick rewrites.
_cache_data: dict[str, Any] | None = None
_cache_signature: tuple[int, int, int, int] | None = None
_storage_lock = threading.RLock()


class HookApprovalStorageError(RuntimeError):
    """Raised when approval state cannot be updated safely."""


def _approvals_path() -> Path:
    return Path.home() / ".koder" / _APPROVALS_FILE


def _approvals_lock_path() -> Path:
    path = _approvals_path()
    return path.with_name(f"{path.name}.lock")


def _canonical_project_path(project_root: Path) -> str:
    return str(project_root.resolve())


def _project_key(project_root: Path) -> str:
    return hashlib.sha256(_canonical_project_path(project_root).encode()).hexdigest()[:16]


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _no_follow_flag() -> int:
    return getattr(os, "O_NOFOLLOW", 0)


def _running_on_windows() -> bool:
    return os.name == "nt"


def _stat_is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _path_is_link_or_reparse(path: Path, file_stat: os.stat_result) -> bool:
    del path
    return stat.S_ISLNK(file_stat.st_mode) or _stat_is_reparse_point(file_stat)


def _windows_handle_is_reparse_point(fd: int) -> bool:
    """Inspect the actual Windows handle, not only the path entry."""
    if not _running_on_windows():
        return False

    try:  # pragma: win32 cover
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [
                ("file_attributes", wintypes.DWORD),
                ("reparse_tag", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        info = FileAttributeTagInfo()
        handle = msvcrt.get_osfhandle(fd)
        # FILE_INFO_BY_HANDLE_CLASS.FileAttributeTagInfo
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            raise OSError(error, "GetFileInformationByHandleEx failed")
        return bool(info.file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT)
    except (ImportError, AttributeError) as exc:  # pragma: win32 cover
        raise OSError("Windows reparse-point handle validation is unavailable") from exc


def _safe_lstat(path: Path, *, allow_missing: bool) -> os.stat_result | None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise
    if _path_is_link_or_reparse(path, file_stat):
        raise HookApprovalStorageError(
            f"refusing symlink or reparse-point hook approval storage path: {path}"
        )
    return file_stat


def _regular_file_signature(file_stat: os.stat_result) -> tuple[int, int, int, int] | None:
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or getattr(file_stat, "st_nlink", 1) != 1
        or _stat_is_reparse_point(file_stat)
    ):
        return None
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mtime_ns,
        file_stat.st_size,
    )


def _secure_storage_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        directory_stat = _safe_lstat(path, allow_missing=False)
    except OSError as exc:
        raise HookApprovalStorageError(
            f"cannot access hook approval directory {path}: {exc}"
        ) from exc
    if directory_stat is None or not stat.S_ISDIR(directory_stat.st_mode):
        raise HookApprovalStorageError(
            f"refusing hook approval storage through non-directory or symlink: {path}"
        )


def _validate_open_regular_file(
    path: Path,
    fd: int,
    *,
    before_stat: os.stat_result | None,
) -> os.stat_result:
    try:
        opened_stat = os.fstat(fd)
        if _regular_file_signature(opened_stat) is None:
            raise HookApprovalStorageError(
                f"refusing non-regular or multiply-linked hook approval file: {path}"
            )
        if _windows_handle_is_reparse_point(fd):
            raise HookApprovalStorageError(
                f"refusing reparse-point hook approval file handle: {path}"
            )
        current_stat = _safe_lstat(path, allow_missing=False)
    except OSError as exc:
        raise HookApprovalStorageError(
            f"cannot validate hook approval storage path {path}: {exc}"
        ) from exc

    if current_stat is None or _regular_file_signature(current_stat) is None:
        raise HookApprovalStorageError(
            f"refusing non-regular or replaced hook approval file: {path}"
        )
    if not os.path.samestat(opened_stat, current_stat):
        raise HookApprovalStorageError(
            f"hook approval storage path changed while opening it: {path}"
        )
    if before_stat is not None and not os.path.samestat(before_stat, opened_stat):
        raise HookApprovalStorageError(
            f"hook approval storage path changed before it was opened: {path}"
        )
    return opened_stat


def _open_regular_file_no_follow(path: Path, flags: int, mode: int = 0o600) -> int:
    no_follow = _no_follow_flag()
    before_stat: os.stat_result | None = None
    if not no_follow:
        try:
            before_stat = _safe_lstat(path, allow_missing=bool(flags & os.O_CREAT))
        except OSError as exc:
            raise HookApprovalStorageError(
                f"refusing unsafe hook approval storage path {path}: {exc}"
            ) from exc
    try:
        fd = os.open(path, flags | no_follow, mode)
    except OSError as exc:
        raise HookApprovalStorageError(
            f"refusing unsafe hook approval storage path {path}: {exc}"
        ) from exc
    try:
        _validate_open_regular_file(path, fd, before_stat=before_stat)
    except HookApprovalStorageError:
        os.close(fd)
        raise
    return fd


def _unix_try_lock_descriptor(fd: int) -> None:
    """Acquire a non-blocking exclusive lock on an already-validated descriptor."""
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unix_unlock_descriptor(fd: int) -> None:
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def _windows_lock_descriptor(fd: int) -> None:
    """Lock the validated Windows file handle without reopening its pathname."""
    try:  # pragma: win32 cover
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class Overlapped(ctypes.Structure):
            _fields_ = [
                ("internal", ctypes.c_size_t),
                ("internal_high", ctypes.c_size_t),
                ("offset", wintypes.DWORD),
                ("offset_high", wintypes.DWORD),
                ("event", wintypes.HANDLE),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LockFileEx.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(Overlapped),
        ]
        kernel32.LockFileEx.restype = wintypes.BOOL
        overlapped = Overlapped()
        handle = msvcrt.get_osfhandle(fd)
        lockfile_exclusive_lock = 0x00000002
        lockfile_fail_immediately = 0x00000001
        if not kernel32.LockFileEx(
            handle,
            lockfile_exclusive_lock | lockfile_fail_immediately,
            0,
            0xFFFFFFFF,
            0xFFFFFFFF,
            ctypes.byref(overlapped),
        ):
            error = ctypes.get_last_error()
            if error in {32, 33}:  # sharing/lock violation
                raise BlockingIOError(error, "hook approval lock is held")
            raise OSError(error, "LockFileEx failed")
    except (ImportError, AttributeError) as exc:  # pragma: win32 cover
        raise OSError("Windows descriptor locking is unavailable") from exc


def _windows_unlock_descriptor(fd: int) -> None:
    try:  # pragma: win32 cover
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class Overlapped(ctypes.Structure):
            _fields_ = [
                ("internal", ctypes.c_size_t),
                ("internal_high", ctypes.c_size_t),
                ("offset", wintypes.DWORD),
                ("offset_high", wintypes.DWORD),
                ("event", wintypes.HANDLE),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.UnlockFileEx.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(Overlapped),
        ]
        kernel32.UnlockFileEx.restype = wintypes.BOOL
        overlapped = Overlapped()
        handle = msvcrt.get_osfhandle(fd)
        if not kernel32.UnlockFileEx(
            handle,
            0,
            0xFFFFFFFF,
            0xFFFFFFFF,
            ctypes.byref(overlapped),
        ):
            error = ctypes.get_last_error()
            raise OSError(error, "UnlockFileEx failed")
    except (ImportError, AttributeError) as exc:  # pragma: win32 cover
        raise OSError("Windows descriptor unlocking is unavailable") from exc


def _try_lock_descriptor(fd: int) -> None:
    if _running_on_windows():
        _windows_lock_descriptor(fd)
    else:
        _unix_try_lock_descriptor(fd)


def _unlock_descriptor(fd: int) -> None:
    if _running_on_windows():
        _windows_unlock_descriptor(fd)
    else:
        _unix_unlock_descriptor(fd)


def _acquire_descriptor_lock(fd: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            _try_lock_descriptor(fd)
            return
        except BlockingIOError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("hook approval lock acquisition timed out") from exc
            time.sleep(min(_LOCK_RETRY_SECONDS, remaining))


def _close_lock_descriptor(fd: int) -> OSError | None:
    try:
        os.close(fd)
    except OSError as exc:
        return exc
    return None


@contextmanager
def _locked_approval_storage() -> Iterator[Callable[[], None]]:
    """Serialize approval read-modify-write operations across processes."""
    with _storage_lock:
        path = _approvals_path()
        _secure_storage_directory(path.parent)
        lock_path = _approvals_lock_path()
        lock_fd = _open_regular_file_no_follow(lock_path, os.O_RDWR | os.O_CREAT)
        acquired = False

        def validate_lock_identity() -> None:
            _validate_open_regular_file(lock_path, lock_fd, before_stat=None)

        try:
            try:
                _acquire_descriptor_lock(lock_fd, _LOCK_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise HookApprovalStorageError(
                    f"timed out waiting for hook approval storage lock: {lock_path}"
                ) from exc
            except OSError as exc:
                raise HookApprovalStorageError(
                    f"cannot safely lock hook approval storage at {lock_path}: {exc}"
                ) from exc
            acquired = True
            validate_lock_identity()
            yield validate_lock_identity
        finally:
            active_error = sys.exc_info()[1]
            cleanup_error: OSError | None = None
            if acquired:
                try:
                    _unlock_descriptor(lock_fd)
                except OSError as exc:
                    cleanup_error = exc
            close_error = _close_lock_descriptor(lock_fd)
            if cleanup_error is None:
                cleanup_error = close_error
            if cleanup_error is not None:
                if active_error is not None:
                    if hasattr(active_error, "add_note"):
                        active_error.add_note(
                            f"hook approval lock cleanup also failed: {cleanup_error}"
                        )
                else:
                    raise HookApprovalStorageError(
                        f"failed to release hook approval storage lock at {lock_path}: "
                        f"{cleanup_error}"
                    ) from cleanup_error


def _load_approvals_from_disk(
    *,
    use_cache: bool,
    fail_unsafe: bool = False,
) -> dict[str, Any]:
    """Read approval JSON without following a replaced symlink."""
    global _cache_data, _cache_signature
    path = _approvals_path()
    try:
        before_stat = _safe_lstat(path, allow_missing=False)
    except FileNotFoundError:
        _invalidate_cache()
        return {}
    except HookApprovalStorageError:
        _invalidate_cache()
        if fail_unsafe:
            raise
        return {}
    except OSError:
        _invalidate_cache()
        return {}

    before_signature = _regular_file_signature(before_stat) if before_stat is not None else None
    if before_signature is None:
        _invalidate_cache()
        return {}
    if use_cache and _cache_data is not None and before_signature == _cache_signature:
        return _cache_data

    try:
        fd = _open_regular_file_no_follow(path, os.O_RDONLY)
    except HookApprovalStorageError:
        _invalidate_cache()
        if fail_unsafe:
            raise
        return {}
    try:
        opened_signature = _regular_file_signature(os.fstat(fd))
        if opened_signature != before_signature:
            _invalidate_cache()
            return {}
        with os.fdopen(fd, "r", encoding="utf-8") as approval_file:
            fd = -1
            loaded = json.load(approval_file)
    except (json.JSONDecodeError, OSError, UnicodeError):
        _invalidate_cache()
        return {}
    finally:
        if fd >= 0:
            os.close(fd)

    if not isinstance(loaded, dict):
        _invalidate_cache()
        return {}
    if use_cache:
        _cache_data = loaded
        _cache_signature = before_signature
    return loaded


def _atomic_write_approvals(data: Mapping[str, Any]) -> None:
    """Atomically replace approval state without following destination links."""
    path = _approvals_path()
    _secure_storage_directory(path.parent)
    try:
        destination_stat = _safe_lstat(path, allow_missing=True)
    except OSError as exc:
        raise HookApprovalStorageError(f"cannot inspect hook approval file {path}: {exc}") from exc
    if destination_stat is not None and _regular_file_signature(destination_stat) is None:
        raise HookApprovalStorageError(
            f"refusing to replace non-regular or linked hook approval file: {path}"
        )

    encoded = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temp_fd = -1
    temp_name = ""
    try:
        temp_fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        if hasattr(os, "fchmod"):
            os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "wb") as temp_file:
            temp_fd = -1
            temp_file.write(encoded)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, path)
        temp_name = ""
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as exc:
        raise HookApprovalStorageError(
            f"failed to persist hook approval state at {path}: {exc}"
        ) from exc
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def load_project_hook_settings(project_root: Path) -> dict[str, dict[str, Any]]:
    """Read project and local settings used by the project hook trust gate."""
    project_root = project_root.resolve()
    sources: dict[str, dict[str, Any]] = {}
    for source, path in (
        (_PROJECT_SOURCE, project_root / ".koder" / "settings.json"),
        (_LOCAL_SOURCE, project_root / ".koder" / "settings.local.json"),
    ):
        if path.exists():
            sources[source] = _load_json_object(path)
    return sources


def _canonical_hook(hook: Any) -> dict[str, Any] | None:
    if not isinstance(hook, dict):
        return None
    return {field: hook[field] for field in _EXECUTABLE_HOOK_FIELDS if field in hook}


def _canonical_source_payload(settings: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if settings.get("disableAllHooks") is True:
        payload["disableAllHooks"] = True

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return payload

    canonical_hooks: dict[str, list[dict[str, Any]]] = {}
    for event_name, groups in hooks.items():
        if not isinstance(event_name, str) or not isinstance(groups, list):
            continue
        canonical_groups: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            canonical_group: dict[str, Any] = {}
            if "matcher" in group:
                canonical_group["matcher"] = group["matcher"]
            raw_hooks = group.get("hooks")
            if isinstance(raw_hooks, list):
                canonical_group["hooks"] = [
                    canonical
                    for raw_hook in raw_hooks
                    if (canonical := _canonical_hook(raw_hook)) is not None
                ]
            else:
                canonical_group["hooks"] = []
            canonical_groups.append(canonical_group)
        canonical_hooks[event_name] = canonical_groups
    if canonical_hooks:
        payload["hooks"] = canonical_hooks
    return payload


def _normalize_sources(
    settings_or_sources: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    """Accept one settings object or the project/local source mapping."""
    if any(source in settings_or_sources for source in (_PROJECT_SOURCE, _LOCAL_SOURCE)):
        return {
            source: settings
            for source in (_PROJECT_SOURCE, _LOCAL_SOURCE)
            if isinstance((settings := settings_or_sources.get(source)), Mapping)
        }
    return {_PROJECT_SOURCE: settings_or_sources}


def canonical_project_hooks_payload(settings_or_sources: Mapping[str, Any]) -> str:
    """Serialize executable project-hook settings deterministically.

    The source name is retained because moving a hook between project and local
    settings changes dispatch identity and once-only behavior. Empty sources
    are omitted so adding unrelated settings does not churn approval.
    """
    sources = _normalize_sources(settings_or_sources)
    canonical_sources = []
    for source in (_PROJECT_SOURCE, _LOCAL_SOURCE):
        settings = sources.get(source)
        if settings is None:
            continue
        payload = _canonical_source_payload(settings)
        if payload:
            canonical_sources.append({"source": source, "payload": payload})
    canonical = {
        "payload_schema_version": _PAYLOAD_SCHEMA_VERSION,
        "sources": canonical_sources,
    }
    return json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def project_hooks_digest(settings_or_sources: Mapping[str, Any]) -> str:
    """Return the SHA-256 digest of the canonical executable hook payload."""
    payload = canonical_project_hooks_payload(settings_or_sources)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_approvals() -> dict[str, Any]:
    """Load approvals with metadata-based caching."""
    return _load_approvals_from_disk(use_cache=True)


def _invalidate_cache() -> None:
    global _cache_data, _cache_signature
    _cache_data = None
    _cache_signature = None


def project_hooks_approval_error(
    project_root: Path,
    settings_by_source: Mapping[str, Any] | None = None,
) -> str | None:
    """Return why current project hooks are not approved, or ``None``."""
    data = _load_approvals()
    approved = data.get("approved") if data else None
    record = approved.get(_project_key(project_root)) if isinstance(approved, dict) else None
    if not isinstance(record, dict):
        if isinstance(record, str):
            return "legacy path-only approval is not trusted; review and reapprove current hooks"
        return "project hooks have not been approved"
    if data.get("schema_version") != _APPROVAL_SCHEMA_VERSION:
        return "hook approval record uses an unsupported schema; review and reapprove current hooks"
    if record.get("project_path") != _canonical_project_path(project_root):
        return "hook approval project path does not match the current project"
    if record.get("digest_algorithm") != _DIGEST_ALGORITHM:
        return "hook approval digest algorithm is unsupported"
    if record.get("payload_schema_version") != _PAYLOAD_SCHEMA_VERSION:
        return "hook approval payload schema is unsupported; review and reapprove current hooks"
    current_settings = (
        settings_by_source
        if settings_by_source is not None
        else load_project_hook_settings(project_root)
    )
    if record.get("executable_digest") != project_hooks_digest(current_settings):
        return "executable hook configuration changed; review and reapprove current hooks"
    return None


def is_project_hooks_allowed(
    project_root: Path,
    settings_by_source: Mapping[str, Any] | None = None,
) -> bool:
    """Check whether the current executable hook payload is approved."""
    return project_hooks_approval_error(project_root, settings_by_source) is None


def approve_project_hooks(
    project_root: Path,
    settings_by_source: Mapping[str, Any] | None = None,
    *,
    expected_digest: str | None = None,
) -> str:
    """Approve the current executable hook payload for this project."""
    current_settings = (
        settings_by_source
        if settings_by_source is not None
        else load_project_hook_settings(project_root)
    )
    digest = project_hooks_digest(current_settings)
    if expected_digest is not None and expected_digest != digest:
        raise ValueError("hook payload digest changed; review the current payload before approving")

    with _locked_approval_storage() as validate_lock_identity:
        validate_lock_identity()
        data = _load_approvals_from_disk(use_cache=False, fail_unsafe=True)
        if data.get("schema_version") != _APPROVAL_SCHEMA_VERSION:
            data = {"schema_version": _APPROVAL_SCHEMA_VERSION, "approved": {}}
        approved = data.get("approved")
        if not isinstance(approved, dict):
            approved = {}
            data["approved"] = approved
        approved[_project_key(project_root)] = {
            "project_path": _canonical_project_path(project_root),
            "executable_digest": digest,
            "digest_algorithm": _DIGEST_ALGORITHM,
            "payload_schema_version": _PAYLOAD_SCHEMA_VERSION,
            "approved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        validate_lock_identity()
        _atomic_write_approvals(data)
    _invalidate_cache()
    return digest


def revoke_project_hooks(project_root: Path) -> bool:
    """Remove approval for this project's hooks."""
    with _locked_approval_storage() as validate_lock_identity:
        validate_lock_identity()
        data = _load_approvals_from_disk(use_cache=False, fail_unsafe=True)
        if not data:
            return False
        approved = data.get("approved")
        if not isinstance(approved, dict):
            return False
        removed = approved.pop(_project_key(project_root), None) is not None
        if not removed:
            return False
        validate_lock_identity()
        _atomic_write_approvals(data)
    _invalidate_cache()
    return True
