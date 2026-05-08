"""Validation helpers for the `/add-dir` command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class AddDirectoryResult:
    result_type: str
    directory_path: str | None = None
    absolute_path: str | None = None
    working_dir: str | None = None


def _path_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def validate_directory_for_workspace(
    directory_path: str,
    *,
    workspace_root: Path,
    additional_roots: Iterable[Path] | None = None,
) -> AddDirectoryResult:
    """Validate a directory before adding it as a working directory."""
    if not directory_path.strip():
        return AddDirectoryResult(result_type="emptyPath")

    absolute_path = Path(directory_path).expanduser().resolve()

    try:
        if not absolute_path.exists():
            return AddDirectoryResult(
                result_type="pathNotFound",
                directory_path=directory_path,
                absolute_path=str(absolute_path),
            )
        if not absolute_path.is_dir():
            return AddDirectoryResult(
                result_type="notADirectory",
                directory_path=directory_path,
                absolute_path=str(absolute_path),
            )
    except OSError as exc:
        if exc.errno in {2, 13, 20, 1}:
            return AddDirectoryResult(
                result_type="pathNotFound",
                directory_path=directory_path,
                absolute_path=str(absolute_path),
            )
        raise

    roots = [workspace_root.resolve(), *(root.resolve() for root in additional_roots or ())]
    for working_dir in roots:
        if _path_within(absolute_path, working_dir):
            return AddDirectoryResult(
                result_type="alreadyInWorkingDirectory",
                directory_path=directory_path,
                absolute_path=str(absolute_path),
                working_dir=str(working_dir),
            )

    return AddDirectoryResult(result_type="success", absolute_path=str(absolute_path))


def add_dir_help_message(result: AddDirectoryResult) -> str:
    """Render a user-facing validation message."""
    if result.result_type == "emptyPath":
        return "Please provide a directory path."
    if result.result_type == "pathNotFound":
        return f"Path {result.absolute_path} was not found."
    if result.result_type == "notADirectory":
        parent_dir = str(Path(result.absolute_path or "").parent)
        return (
            f"{result.directory_path} is not a directory. "
            f"Did you mean to add the parent directory {parent_dir}?"
        )
    if result.result_type == "alreadyInWorkingDirectory":
        return (
            f"{result.directory_path} is already accessible within the existing "
            f"working directory {result.working_dir}."
        )
    if result.result_type == "success":
        return f"Added {result.absolute_path} as a working directory."
    return "Unable to validate directory."
