"""Symlink-free, directory-fd-pinned filesystem helpers for plugins."""

from __future__ import annotations

import errno
import json
import os
import secrets
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator

from .name_validation import canonical_plugin_name


class PluginPathError(ValueError):
    """Raised when a plugin path cannot be used without crossing trust boundaries."""


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def _open_directory_no_symlinks(path: Path, *, create: bool = False) -> tuple[Path, int]:
    """Open a directory while rejecting symlinks in every path component."""
    absolute = _absolute_path(path)
    descriptor = os.open(os.sep, _DIRECTORY_FLAGS)
    try:
        for component in absolute.parts[1:]:
            if not component:
                continue
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise PluginPathError(f"Directory does not exist: {absolute}") from None
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                try:
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except OSError as exc:
                    raise PluginPathError(
                        f"Refusing directory changed during creation: {absolute}"
                    ) from exc
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise PluginPathError(
                        f"Refusing directory with a symlink or non-directory component: {absolute}"
                    ) from exc
                raise PluginPathError(f"Cannot open directory '{absolute}': {exc}") from exc
            os.close(descriptor)
            descriptor = child
        return absolute, descriptor
    except Exception:
        os.close(descriptor)
        raise


def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino, stat.S_IFMT(first.st_mode)) == (
        second.st_dev,
        second.st_ino,
        stat.S_IFMT(second.st_mode),
    )


def validate_component_path(value: object, *, field_name: str) -> tuple[str | None, str]:
    """Validate one portable relative path declared by a plugin manifest."""
    if not isinstance(value, str):
        return None, f"'{field_name}' must be a string path"
    if not value:
        return None, f"'{field_name}' must not be empty"
    if "\\" in value:
        return None, f"'{field_name}' must use a safe relative path without backslashes"
    windows_path = PureWindowsPath(value)
    if Path(value).is_absolute() or windows_path.is_absolute() or windows_path.drive:
        return None, f"Absolute path not allowed in '{field_name}': {value}"
    components = value.split("/")
    if ".." in components:
        return None, f"Path traversal (..) not allowed in '{field_name}': {value}"
    if any(component in {"", ".", ".."} for component in components[:-1]) or components[-1] in {
        ".",
        "..",
    }:
        return None, f"Unsafe relative path not allowed in '{field_name}': {value}"
    normalized = "/".join(component for component in components if component)
    if not normalized:
        return None, f"'{field_name}' must not be empty"
    return normalized, ""


def _open_relative_no_symlinks(
    root_fd: int,
    relative: str,
    *,
    expect: str | None = None,
) -> int:
    """Open a relative path beneath ``root_fd`` without following symlinks."""
    components = relative.split("/")
    current = os.dup(root_fd)
    try:
        for index, component in enumerate(components):
            final = index == len(components) - 1
            flags = _FILE_READ_FLAGS if final and expect == "file" else _DIRECTORY_FLAGS
            child = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = child
        result = os.fstat(current)
        if expect == "file" and not stat.S_ISREG(result.st_mode):
            raise PluginPathError(f"Plugin component is not a regular file: {relative}")
        if expect == "directory" and not stat.S_ISDIR(result.st_mode):
            raise PluginPathError(f"Plugin component is not a directory: {relative}")
        return current
    except OSError as exc:
        os.close(current)
        if isinstance(exc, FileNotFoundError):
            raise
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise PluginPathError(f"Plugin component traverses a symlink: {relative}") from exc
        raise PluginPathError(f"Cannot open plugin component '{relative}': {exc}") from exc
    except Exception:
        os.close(current)
        raise


def resolve_plugin_component(
    plugin_dir: Path,
    declared_path: object | None,
    *,
    default: str,
    field_name: str,
    expect: str,
) -> Path | None:
    """Resolve an existing manifest component, failing closed on every symlink."""
    value = default if declared_path is None else declared_path
    relative, error = validate_component_path(value, field_name=field_name)
    if relative is None:
        raise PluginPathError(error)
    root, root_fd = _open_directory_no_symlinks(plugin_dir)
    try:
        try:
            component_fd = _open_relative_no_symlinks(root_fd, relative, expect=expect)
        except FileNotFoundError:
            return None
        os.close(component_fd)
        candidate = root.joinpath(*relative.split("/"))
        if candidate != root and root not in candidate.parents:
            raise PluginPathError(f"Plugin component escapes plugin root: {relative}")
        return candidate
    finally:
        os.close(root_fd)


@contextmanager
def open_plugin_component(
    plugin_dir: Path,
    declared_path: object | None,
    *,
    default: str,
    field_name: str,
    expect: str,
):
    """Yield a descriptor-pinned component path for the duration of its use.

    Returning an ordinary path after validation leaves a race window in which
    an attacker can replace the plugin directory or component.  The yielded
    ``/dev/fd`` (macOS) or ``/proc/self/fd`` (Linux) path addresses the already
    opened object.  Platforms without either descriptor filesystem receive a
    private descriptor-derived file snapshot instead.
    """
    value = default if declared_path is None else declared_path
    relative, error = validate_component_path(value, field_name=field_name)
    if relative is None:
        raise PluginPathError(error)
    root, root_fd = _open_directory_no_symlinks(plugin_dir)
    component_fd = -1
    try:
        try:
            component_fd = _open_relative_no_symlinks(root_fd, relative, expect=expect)
        except FileNotFoundError:
            yield None
            return
        if expect == "directory":
            snapshot = _snapshot_directory_fd(component_fd)
            try:
                yield snapshot
            finally:
                shutil.rmtree(snapshot, ignore_errors=True)
            return

        descriptor_path = _descriptor_path(component_fd)
        if descriptor_path is not None:
            yield descriptor_path
            return

        snapshot = _snapshot_file_fd(component_fd)
        try:
            yield snapshot
        finally:
            try:
                snapshot.unlink()
            except FileNotFoundError:
                pass
    finally:
        if component_fd >= 0:
            os.close(component_fd)
        os.close(root_fd)


def path_entry_exists(path: Path) -> bool:
    """Return whether a directory entry exists without following its final symlink."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def remove_path_entry(path: Path) -> None:
    """Remove one path entry after opening its parent without symlink traversal."""
    parent, parent_fd = _open_directory_no_symlinks(path.parent)
    try:
        _remove_entry_at(parent_fd, path.name)
    finally:
        os.close(parent_fd)


def _remove_entry_at(parent_fd: int, name: str) -> None:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(entry.st_mode) and not stat.S_ISLNK(entry.st_mode):
        child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        try:
            opened = os.fstat(child_fd)
            if not _same_identity(entry, opened):
                raise PluginPathError(f"Directory entry changed while removing: {name}")
            for child in os.listdir(child_fd):
                _remove_entry_at(child_fd, child)
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=parent_fd)
    else:
        os.unlink(name, dir_fd=parent_fd)


def _copy_regular_file(source_fd: int, destination_fd: int) -> None:
    while True:
        chunk = os.read(source_fd, 1024 * 1024)
        if not chunk:
            break
        view = memoryview(chunk)
        while view:
            written = os.write(destination_fd, view)
            view = view[written:]


def _descriptor_path(descriptor: int) -> Path | None:
    for root in (Path("/dev/fd"), Path("/proc/self/fd")):
        if root.exists():
            return root / str(descriptor)
    return None


def _snapshot_file_fd(source_fd: int) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="koder-plugin-file-snapshot-")
    snapshot = Path(raw_path)
    try:
        os.lseek(source_fd, 0, os.SEEK_SET)
        _copy_regular_file(source_fd, descriptor)
        os.fsync(descriptor)
    except Exception:
        try:
            snapshot.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(descriptor)
    return snapshot


def _copy_directory_contents(source_fd: int, destination_fd: int, *, relative: Path) -> None:
    for name in sorted(os.listdir(source_fd)):
        before = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        child_relative = relative / name
        if stat.S_ISLNK(before.st_mode):
            raise PluginPathError(f"Plugin source contains a symlink: {child_relative}")
        if stat.S_ISDIR(before.st_mode):
            child_source = os.open(name, _DIRECTORY_FLAGS, dir_fd=source_fd)
            try:
                if not _same_identity(before, os.fstat(child_source)):
                    raise PluginPathError(
                        f"Plugin source entry changed during copy: {child_relative}"
                    )
                os.mkdir(name, mode=stat.S_IMODE(before.st_mode) or 0o700, dir_fd=destination_fd)
                child_destination = os.open(name, _DIRECTORY_FLAGS, dir_fd=destination_fd)
                try:
                    _copy_directory_contents(
                        child_source,
                        child_destination,
                        relative=child_relative,
                    )
                finally:
                    os.close(child_destination)
            finally:
                os.close(child_source)
            continue
        if not stat.S_ISREG(before.st_mode):
            raise PluginPathError(f"Plugin source contains a special file: {child_relative}")

        source_file = os.open(name, _FILE_READ_FLAGS, dir_fd=source_fd)
        try:
            if not _same_identity(before, os.fstat(source_file)):
                raise PluginPathError(f"Plugin source entry changed during copy: {child_relative}")
            destination_file = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=destination_fd,
            )
            try:
                _copy_regular_file(source_file, destination_file)
                os.fchmod(destination_file, stat.S_IMODE(before.st_mode) & 0o777)
            finally:
                os.close(destination_file)
        finally:
            os.close(source_file)


def copy_tree_without_links(source: Path, destination_fd: int) -> None:
    """Copy a source directory into an empty pinned directory without links/special files."""
    _source_path, source_fd = _open_directory_no_symlinks(source)
    try:
        _copy_directory_contents(source_fd, destination_fd, relative=Path("."))
        os.fsync(destination_fd)
    finally:
        os.close(source_fd)


def _snapshot_directory_fd(source_fd: int) -> Path:
    # macOS exposes its temp directory through /var -> /private/var. Resolve
    # this Koder-created path once so subsequent no-symlink opens do not reject
    # the operating system's compatibility alias.
    snapshot = Path(tempfile.mkdtemp(prefix="koder-plugin-snapshot-")).resolve()
    try:
        snapshot_fd = os.open(snapshot, _DIRECTORY_FLAGS)
        try:
            _copy_directory_contents(source_fd, snapshot_fd, relative=Path("."))
            os.fsync(snapshot_fd)
        finally:
            os.close(snapshot_fd)
    except Exception:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise
    return snapshot


@contextmanager
def snapshot_plugin_tree(plugin_dir: Path) -> Iterator[Path]:
    """Yield a private runtime snapshot and remove it when the scope exits."""
    _source_path, source_fd = _open_directory_no_symlinks(plugin_dir)
    try:
        snapshot = _snapshot_directory_fd(source_fd)
    finally:
        os.close(source_fd)
    try:
        yield snapshot
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)


@dataclass
class PinnedDirectory:
    """A directory entry and open descriptor owned by a ``PluginRootGuard``."""

    name: str
    path: Path
    fd: int

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class PluginRootGuard:
    """Pin a symlink-free root and perform mutations relative to its directory fd."""

    def __init__(self, root: Path, *, create: bool = True):
        self.root, self._fd = _open_directory_no_symlinks(root, create=create)
        root_stat = os.fstat(self._fd)
        self._root_identity = (root_stat.st_dev, root_stat.st_ino)

    def dup_fd(self) -> int:
        return os.dup(self._fd)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __del__(self) -> None:  # pragma: no cover - best-effort descriptor cleanup
        try:
            if hasattr(self, "_fd"):
                self.close()
        except OSError:
            pass

    def _verify_fd_identity(self) -> None:
        root_stat = os.fstat(self._fd)
        if (root_stat.st_dev, root_stat.st_ino) != self._root_identity:
            raise PluginPathError(f"Plugin root descriptor identity changed: {self.root}")

    def verify_access_path(self) -> None:
        """Require the public pathname to still identify the pinned directory."""
        _path, descriptor = _open_directory_no_symlinks(self.root)
        try:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != self._root_identity:
                raise PluginPathError(f"Plugin root identity changed: {self.root}")
        finally:
            os.close(descriptor)

    def target(self, name: object, *, reject_symlink: bool = True) -> Path:
        """Return a canonical direct-child path after checking the public root path."""
        self._verify_fd_identity()
        self.verify_access_path()
        canonical_name, error = canonical_plugin_name(name)
        if canonical_name is None:
            raise PluginPathError(f"Invalid plugin name {name!r}: {error}")
        if reject_symlink:
            try:
                mode = os.stat(canonical_name, dir_fd=self._fd, follow_symlinks=False).st_mode
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISLNK(mode):
                    raise PluginPathError(f"Plugin path for '{canonical_name}' is a symlink")
        return self.root / canonical_name

    def entry_exists(self, name: str) -> bool:
        self._verify_fd_identity()
        try:
            os.stat(name, dir_fd=self._fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        return True

    def entry_is_symlink(self, name: str) -> bool:
        try:
            mode = os.stat(name, dir_fd=self._fd, follow_symlinks=False).st_mode
        except FileNotFoundError:
            return False
        return stat.S_ISLNK(mode)

    def list_entries(self) -> list[str]:
        self._verify_fd_identity()
        return sorted(os.listdir(self._fd))

    def staging_dir(self, name: str, *, purpose: str = "stage") -> PinnedDirectory:
        """Create a private, fixed-size random directory inside the pinned root."""
        self._verify_fd_identity()
        canonical_name, error = canonical_plugin_name(name)
        if canonical_name is None:
            raise PluginPathError(f"Invalid plugin name {name!r}: {error}")
        for _attempt in range(128):
            temporary_name = f".koder-{purpose}-{secrets.token_hex(12)}"
            try:
                os.mkdir(temporary_name, mode=0o700, dir_fd=self._fd)
            except FileExistsError:
                continue
            descriptor = os.open(temporary_name, _DIRECTORY_FLAGS, dir_fd=self._fd)
            return PinnedDirectory(temporary_name, self.root / temporary_name, descriptor)
        raise PluginPathError("Could not allocate a private plugin staging directory")

    def replace(self, source_name: str, target_name: str) -> None:
        self._verify_fd_identity()
        os.replace(source_name, target_name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
        os.fsync(self._fd)

    def remove_entry_name(self, name: str) -> None:
        self._verify_fd_identity()
        _remove_entry_at(self._fd, name)
        os.fsync(self._fd)

    def remove_entry(self, path: Path) -> None:
        if path.parent != self.root:
            raise PluginPathError(f"Refusing to remove path outside plugin root: {path}")
        self.remove_entry_name(path.name)

    def read_json(self, name: str) -> dict[str, Any] | None:
        try:
            descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=self._fd)
        except FileNotFoundError:
            return None
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise PluginPathError(f"Plugin metadata is not a regular file: {name}")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                loaded = json.load(stream)
            if not isinstance(loaded, dict):
                raise PluginPathError(f"Plugin metadata must be a JSON object: {name}")
            return loaded
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def write_json_atomic(self, name: str, data: dict[str, Any]) -> None:
        temporary_name = f".{name}.{secrets.token_hex(12)}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=self._fd,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
            os.fsync(self._fd)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=self._fd)
            except FileNotFoundError:
                pass

    def unlink(self, name: str) -> None:
        try:
            os.unlink(name, dir_fd=self._fd)
        except FileNotFoundError:
            return
        os.fsync(self._fd)
