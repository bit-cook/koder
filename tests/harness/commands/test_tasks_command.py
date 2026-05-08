import asyncio

from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.harness.memory.auto_dream import default_auto_dream_task_storage


def _run(command: str, *, handler: HarnessInteractiveCommandHandler | None = None) -> str:
    handler = handler or HarnessInteractiveCommandHandler(emit_console=False)
    return asyncio.run(handler.handle_slash_input(command, scheduler=None))


def test_tasks_command_includes_auto_dream_task_records(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    storage = default_auto_dream_task_storage()
    task = storage.create(
        "AutoDream memory consolidation",
        description="fixture",
        metadata={"kind": "auto-dream", "memories_written": 2, "errors": []},
    )
    storage.update(task.id, status="completed")

    output = _run("/tasks")

    assert f"auto-dream/{task.id}" in output
    assert "AutoDream memory consolidation status=completed" in output
    assert "memories=2" in output


def test_tasks_command_reports_malformed_auto_dream_records(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    storage = default_auto_dream_task_storage()
    task = storage.create(
        "AutoDream memory consolidation",
        description="fixture",
        metadata={"kind": "auto-dream", "memories_written": 1, "errors": []},
    )
    storage.update(task.id, status="completed")
    (storage.root / "broken.json").write_text("{not json", encoding="utf-8")

    output = _run("/tasks")

    assert f"auto-dream/{task.id}" in output
    assert "memories=1" in output
    assert "auto-dream/malformed: broken.json" in output
