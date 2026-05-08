"""Local team memory synchronization helpers."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

MAX_TEAM_MEMORY_FILE_BYTES = 250_000


@dataclass(frozen=True)
class TeamMemorySyncResult:
    """Result from syncing project and runtime team memory directories."""

    team_id: str
    project_dir: Path
    runtime_dir: Path
    copied_to_project: int
    copied_to_runtime: int
    unchanged: int
    skipped: int


@dataclass(frozen=True)
class TeamMemoryStatus:
    """Current team memory file counts."""

    team_id: str
    project_dir: Path
    runtime_dir: Path
    project_files: int
    runtime_files: int


def _memory_files(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}

    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if path.name.endswith(".lock"):
            continue
        files[Path(*rel_parts).as_posix()] = path
    return files


def _copy_memory_file(source: Path, destination: Path) -> bool:
    try:
        if source.stat().st_size > MAX_TEAM_MEMORY_FILE_BYTES:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return True
    except OSError:
        return False


def team_memory_status(
    *,
    team_id: str,
    project_dir: Path,
    runtime_dir: Path,
) -> TeamMemoryStatus:
    """Return team memory file counts for both local roots."""

    return TeamMemoryStatus(
        team_id=team_id,
        project_dir=project_dir,
        runtime_dir=runtime_dir,
        project_files=len(_memory_files(project_dir)),
        runtime_files=len(_memory_files(runtime_dir)),
    )


def sync_team_memory_dirs(
    *,
    team_id: str,
    project_dir: Path,
    runtime_dir: Path,
) -> TeamMemorySyncResult:
    """Synchronize project and runtime team memory directories locally."""

    project_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)

    copied_to_project = 0
    copied_to_runtime = 0
    unchanged = 0
    skipped = 0

    project_files = _memory_files(project_dir)
    runtime_files = _memory_files(runtime_dir)
    keys = sorted(set(project_files) | set(runtime_files))

    for key in keys:
        project_path = project_files.get(key)
        runtime_path = runtime_files.get(key)

        if project_path is not None and runtime_path is None:
            if _copy_memory_file(project_path, runtime_dir / key):
                copied_to_runtime += 1
            else:
                skipped += 1
            continue

        if runtime_path is not None and project_path is None:
            if _copy_memory_file(runtime_path, project_dir / key):
                copied_to_project += 1
            else:
                skipped += 1
            continue

        if project_path is None or runtime_path is None:
            continue

        try:
            project_bytes = project_path.read_bytes()
            runtime_bytes = runtime_path.read_bytes()
        except OSError:
            skipped += 1
            continue

        if project_bytes == runtime_bytes:
            unchanged += 1
            continue

        try:
            project_mtime = project_path.stat().st_mtime_ns
            runtime_mtime = runtime_path.stat().st_mtime_ns
        except OSError:
            skipped += 1
            continue

        if project_mtime >= runtime_mtime:
            if _copy_memory_file(project_path, runtime_path):
                copied_to_runtime += 1
            else:
                skipped += 1
        elif _copy_memory_file(runtime_path, project_path):
            copied_to_project += 1
        else:
            skipped += 1

    return TeamMemorySyncResult(
        team_id=team_id,
        project_dir=project_dir,
        runtime_dir=runtime_dir,
        copied_to_project=copied_to_project,
        copied_to_runtime=copied_to_runtime,
        unchanged=unchanged,
        skipped=skipped,
    )
