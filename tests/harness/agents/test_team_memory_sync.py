from __future__ import annotations

import os
import time
from pathlib import Path

from koder_agent.harness.agents.teams.memory_sync import sync_team_memory_dirs
from koder_agent.harness.agents.teams.service import TeamService


def test_team_memory_sync_copies_project_memory_to_runtime(tmp_path: Path):
    service = TeamService.for_test(root=tmp_path / ".koder", cwd=tmp_path)
    team_id = service.create_team("sync-team")
    project_memory = tmp_path / ".koder" / "team-memory" / team_id / "MEMORY.md"
    project_memory.parent.mkdir(parents=True)
    project_memory.write_text("Shared test notes\n", encoding="utf-8")

    result = service.sync_team_memory(team_id)

    runtime_memory = service.teams_root / team_id / "memory" / "MEMORY.md"
    assert result.copied_to_runtime == 1
    assert result.copied_to_project == 0
    assert runtime_memory.read_text(encoding="utf-8") == "Shared test notes\n"


def test_team_memory_sync_copies_runtime_memory_to_project(tmp_path: Path):
    service = TeamService.for_test(root=tmp_path / ".koder", cwd=tmp_path)
    team_id = service.create_team("sync-team")
    runtime_memory = service.teams_root / team_id / "memory" / "worker.md"
    runtime_memory.parent.mkdir(parents=True)
    runtime_memory.write_text("Worker notes\n", encoding="utf-8")

    result = service.sync_team_memory(team_id)

    project_memory = tmp_path / ".koder" / "team-memory" / team_id / "worker.md"
    assert result.copied_to_project == 1
    assert result.copied_to_runtime == 0
    assert project_memory.read_text(encoding="utf-8") == "Worker notes\n"


def test_team_memory_sync_newer_file_wins(tmp_path: Path):
    project_dir = tmp_path / "project"
    runtime_dir = tmp_path / "runtime"
    project_dir.mkdir()
    runtime_dir.mkdir()
    project_file = project_dir / "MEMORY.md"
    runtime_file = runtime_dir / "MEMORY.md"
    project_file.write_text("project\n", encoding="utf-8")
    runtime_file.write_text("runtime\n", encoding="utf-8")
    older = time.time() - 60
    newer = time.time()
    os.utime(project_file, (older, older))
    os.utime(runtime_file, (newer, newer))

    result = sync_team_memory_dirs(
        team_id="sync-team",
        project_dir=project_dir,
        runtime_dir=runtime_dir,
    )

    assert result.copied_to_project == 1
    assert project_file.read_text(encoding="utf-8") == "runtime\n"


def test_team_memory_status_counts_files(tmp_path: Path):
    service = TeamService.for_test(root=tmp_path / ".koder", cwd=tmp_path)
    team_id = service.create_team("sync-team")
    project_memory = tmp_path / ".koder" / "team-memory" / team_id / "MEMORY.md"
    project_memory.parent.mkdir(parents=True)
    project_memory.write_text("Shared test notes\n", encoding="utf-8")

    status = service.team_memory_status(team_id)

    assert status.team_id == team_id
    assert status.project_files == 1
    assert status.runtime_files == 0
