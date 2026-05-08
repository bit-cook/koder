import asyncio
import json

from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler


def _run(command: str, *, handler: HarnessInteractiveCommandHandler, scheduler=None) -> str:
    return asyncio.run(handler.handle_slash_input(command, scheduler=scheduler))


def test_hooks_command_lists_configured_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Edit|Write",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "echo lint",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    handler = HarnessInteractiveCommandHandler()

    output = _run("/hooks", handler=handler)

    assert "hooks:" in output
    assert "count: 1" in output
    assert "PostToolUse" in output
    assert "matcher=Edit|Write" in output
