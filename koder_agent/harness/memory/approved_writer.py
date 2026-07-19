"""Descriptor-relative atomic persistence for approved durable outputs."""

from __future__ import annotations

import errno
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _absolute_root(root: str | Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(str(root))))
    if len(absolute.parts) <= 1:
        return absolute
    trusted_top_level = Path(absolute.anchor) / absolute.parts[1]
    canonical_top_level = Path(os.path.realpath(trusted_top_level))
    return canonical_top_level.joinpath(*absolute.parts[2:])


def _relative_parts(relative: str | Path) -> tuple[str, ...]:
    value = Path(relative)
    if value.is_absolute() or not value.parts:
        raise ValueError("output path must be relative to its trusted root")
    if any(part in {"", ".", ".."} for part in value.parts):
        raise ValueError("output path escapes its trusted root")
    return value.parts


def _open_child_directory(parent_fd: int, name: str, *, create: bool) -> int:
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(f"refusing symlink or non-directory path component: {name}") from exc
        raise


@contextmanager
def _open_directory(
    root: str | Path, relative_parts: tuple[str, ...], *, create: bool
) -> Iterator[int]:
    absolute = _absolute_root(root)
    current_fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in (*absolute.parts[1:], *relative_parts):
            next_fd = _open_child_directory(current_fd, part, create=create)
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        os.close(current_fd)


@contextmanager
def open_trusted_directory(
    root: str | Path,
    relative: str | Path | None = None,
    *,
    create: bool = True,
) -> Iterator[int]:
    """Open a directory beneath a no-follow trusted root."""

    parts = () if relative is None else _relative_parts(relative)
    with _open_directory(root, parts, create=create) as descriptor:
        yield descriptor


def _destination_stat(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _validate_destination(parent_fd: int, name: str, *, exclusive: bool) -> None:
    destination = _destination_stat(parent_fd, name)
    if destination is None:
        return
    if exclusive:
        raise FileExistsError(errno.EEXIST, "output already exists", name)
    if not stat.S_ISREG(destination.st_mode):
        raise ValueError("refusing linked or non-regular output destination")
    if destination.st_nlink != 1:
        raise ValueError("refusing linked output destination")


def write_approved_output(
    root: str | Path,
    relative: str | Path,
    content: str | bytes,
    *,
    exclusive: bool = False,
) -> Path:
    """Write a private, fsynced file without following any path component."""

    parts = _relative_parts(relative)
    parent_parts, name = parts[:-1], parts[-1]
    data = content.encode("utf-8") if isinstance(content, str) else content
    with _open_directory(root, parent_parts, create=True) as parent_fd:
        _validate_destination(parent_fd, name, exclusive=exclusive)
        temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        installed = False
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
            temporary_stat = os.fstat(descriptor)
            if not stat.S_ISREG(temporary_stat.st_mode) or temporary_stat.st_nlink != 1:
                raise ValueError("temporary output file was linked")
            _validate_destination(parent_fd, name, exclusive=exclusive)
            if exclusive:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                os.unlink(temporary_name, dir_fd=parent_fd)
            else:
                os.replace(
                    temporary_name,
                    name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
            installed = True
            os.fsync(parent_fd)
        finally:
            os.close(descriptor)
            if not installed:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
    return _absolute_root(root).joinpath(*parts)


def read_trusted_file(
    root: str | Path,
    relative: str | Path,
    *,
    maximum_bytes: int,
    required_mode: int | None = None,
) -> bytes:
    """Read a bounded regular file without following path components."""

    parts = _relative_parts(relative)
    with _open_directory(root, parts[:-1], create=False) as parent_fd:
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("trusted file is not regular")
            if file_stat.st_nlink != 1:
                raise ValueError("trusted file is linked")
            if required_mode is not None and stat.S_IMODE(file_stat.st_mode) != required_mode:
                raise ValueError("trusted file has unexpected permissions")
            if file_stat.st_size > maximum_bytes:
                raise ValueError("trusted file exceeds size limit")
            chunks: list[bytes] = []
            remaining = maximum_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > maximum_bytes:
                raise ValueError("trusted file exceeds size limit")
            return data
        finally:
            os.close(descriptor)


def trusted_file_exists(root: str | Path, relative: str | Path) -> bool:
    """Check for an entry without following its parent or final component."""

    parts = _relative_parts(relative)
    try:
        with _open_directory(root, parts[:-1], create=False) as parent_fd:
            return _destination_stat(parent_fd, parts[-1]) is not None
    except FileNotFoundError:
        return False


def trusted_file_size(root: str | Path, relative: str | Path) -> int:
    """Return the lstat size of a trusted regular file."""

    parts = _relative_parts(relative)
    with _open_directory(root, parts[:-1], create=False) as parent_fd:
        file_stat = _destination_stat(parent_fd, parts[-1])
        if file_stat is None:
            raise FileNotFoundError(str(relative))
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("trusted file is not regular")
        return file_stat.st_size


def list_trusted_names(root: str | Path, relative: str | Path) -> list[str]:
    """List names from a no-follow trusted directory."""

    try:
        with open_trusted_directory(root, relative, create=False) as descriptor:
            return sorted(os.listdir(descriptor))
    except FileNotFoundError:
        return []


def replace_trusted_file(
    root: str | Path,
    source: str | Path,
    destination: str | Path,
) -> None:
    """Atomically move a file within a trusted root using directory fds."""

    source_parts = _relative_parts(source)
    destination_parts = _relative_parts(destination)
    with _open_directory(root, source_parts[:-1], create=False) as source_fd:
        with _open_directory(root, destination_parts[:-1], create=True) as destination_fd:
            os.replace(
                source_parts[-1],
                destination_parts[-1],
                src_dir_fd=source_fd,
                dst_dir_fd=destination_fd,
            )
            os.fsync(source_fd)
            if destination_fd != source_fd:
                os.fsync(destination_fd)


def unlink_trusted_file(
    root: str | Path,
    relative: str | Path,
    *,
    missing_ok: bool = False,
) -> None:
    """Unlink an entry without following any path component."""

    parts = _relative_parts(relative)
    try:
        with _open_directory(root, parts[:-1], create=False) as parent_fd:
            file_stat = _destination_stat(parent_fd, parts[-1])
            if file_stat is None:
                if missing_ok:
                    return
                raise FileNotFoundError(str(relative))
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("refusing to unlink non-regular trusted file")
            os.unlink(parts[-1], dir_fd=parent_fd)
            os.fsync(parent_fd)
    except FileNotFoundError:
        if not missing_ok:
            raise
