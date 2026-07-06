import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.harness.worktree.service import WorktreeService


def _run(command: str, *, handler: HarnessInteractiveCommandHandler, scheduler=None) -> str:
    return asyncio.run(handler.handle_slash_input(command, scheduler=scheduler))


def test_compact_hooks_fire(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    pre = tmp_path / "precompact.json"
    post = tmp_path / "postcompact.json"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreCompact": [
                        {
                            "matcher": "manual",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{pre}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ],
                    "PostCompact": [
                        {
                            "matcher": "manual",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{post}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    class _Session:
        session_id = "compact-session"

        async def get_items(self):
            return [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}]

    scheduler = SimpleNamespace(session=_Session())
    handler = HarnessInteractiveCommandHandler()

    output = _run("/compact", handler=handler, scheduler=scheduler)

    assert output.startswith("compacted, context size ")
    assert json.loads(pre.read_text(encoding="utf-8"))["event"] == "PreCompact"
    assert json.loads(post.read_text(encoding="utf-8"))["event"] == "PostCompact"


def test_cwd_changed_hook_fires_on_dispatch(tmp_path, monkeypatch):
    from koder_agent.harness.hooks.runtime import dispatch_command_hooks

    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    target = tmp_path / "target"
    marker = tmp_path / "cwd-changed.json"
    target.mkdir()
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "CwdChanged": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    result = dispatch_command_hooks(
        cwd=project,
        event_name="CwdChanged",
        match_value=None,
        payload={"event": "CwdChanged", "cwd": str(target)},
    )

    assert not result.blocked
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "CwdChanged"


def test_cwd_changed_hook_can_register_watch_paths(tmp_path, monkeypatch):
    from koder_agent.harness.hooks.runtime import (
        dispatch_command_hooks,
        poll_file_change_hooks,
        update_watch_paths,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    target = tmp_path / "target"
    watched = tmp_path / "watched.txt"
    marker = tmp_path / "file-changed.json"
    target.mkdir()
    watched.write_text("one", encoding="utf-8")
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "CwdChanged": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f'python -c "print(\'{{\\"hookSpecificOutput\\":{{\\"watchPaths\\":[\\"{watched}\\"]}}}}\')"',
                                }
                            ]
                        }
                    ],
                    "FileChanged": [
                        {
                            "matcher": watched.name,
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    result = dispatch_command_hooks(
        cwd=project,
        event_name="CwdChanged",
        match_value=None,
        payload={"event": "CwdChanged", "cwd": str(target)},
    )
    update_watch_paths(result.watch_paths)
    watched.write_text("two", encoding="utf-8")
    fired = poll_file_change_hooks(project)

    assert fired == 1
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "FileChanged"


def test_worktree_hooks_fire(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    repo = tmp_path / "repo"
    create_marker = tmp_path / "worktree-create.json"
    remove_marker = tmp_path / "worktree-remove.json"
    repo.mkdir()
    (repo / ".koder").mkdir(parents=True)
    (repo / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "WorktreeCreate": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{create_marker}').write_text(sys.stdin.read())\"",
                                }
                            ]
                        }
                    ],
                    "WorktreeRemove": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{remove_marker}').write_text(sys.stdin.read())\"",
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    service = WorktreeService.for_test(repo)
    created = service.create("feature/demo")
    service.exit(created.path)

    assert json.loads(create_marker.read_text(encoding="utf-8"))["event"] == "WorktreeCreate"
    assert json.loads(remove_marker.read_text(encoding="utf-8"))["event"] == "WorktreeRemove"
