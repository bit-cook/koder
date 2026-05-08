from __future__ import annotations

import asyncio
from pathlib import Path

from koder_agent.harness.agents.teams.service import TeamService
from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler


def _run(command: str, *, handler: HarnessInteractiveCommandHandler) -> str:
    return asyncio.run(handler.handle_slash_input(command, scheduler=None)) or ""


def test_peers_memory_sync_command_copies_project_memory(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    service = TeamService.for_test(root=tmp_path / ".koder-runtime", cwd=tmp_path)
    handler = HarnessInteractiveCommandHandler(
        team_service=service,
        emit_console=False,
    )
    team_id = service.create_team("sync-team")
    memory_file = tmp_path / ".koder" / "team-memory" / team_id / "MEMORY.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("Shared command notes\n", encoding="utf-8")

    output = _run(f"/peers memory {team_id} sync", handler=handler)
    status = _run(f"/peers memory {team_id}", handler=handler)

    assert "peers: team memory sync" in output
    assert "copied_to_runtime: 1" in output
    assert "project_files: 1" in status
    assert "runtime_files: 1" in status
