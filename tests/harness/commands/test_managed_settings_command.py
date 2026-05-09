import asyncio
import json

from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler


def _run(command: str, *, handler: HarnessInteractiveCommandHandler) -> str:
    return asyncio.run(handler.handle_slash_input(command, scheduler=None))


def test_managed_settings_command_reports_policy_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir(parents=True)
    (tmp_path / ".koder" / "managed-settings.json").write_text(
        json.dumps(
            {
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo ok"}]}]},
                "sandbox": {"enabled": True, "backend": "unix-local"},
            }
        ),
        encoding="utf-8",
    )
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    output = _run("/managed-settings", handler=handler)

    assert "managed_settings:" in output
    assert "exists: true" in output
    assert "hooks_events: 1" in output
    assert "sandbox_policy_locked: true" in output


def test_managed_settings_command_is_registered():
    handler = HarnessInteractiveCommandHandler(emit_console=False)

    assert "managed-settings" in handler.commands
