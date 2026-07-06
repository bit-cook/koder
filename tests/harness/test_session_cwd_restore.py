"""Session-switch cwd restore and its CwdChanged hook dispatch."""

from __future__ import annotations

import asyncio
import json

from koder_agent.core.session import EnhancedSQLiteSession
from koder_agent.harness.session_flow import restore_session_cwd


def _write_cwd_hook(project, marker) -> None:
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
                                    "command": (
                                        'python -c "import sys, pathlib; '
                                        f"pathlib.Path(r'{marker}').write_text(sys.stdin.read())\""
                                    ),
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def test_restore_session_cwd_changes_dir_and_fires_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    target = tmp_path / "target"
    marker = tmp_path / "cwd-changed.json"
    target.mkdir()
    _write_cwd_hook(project, marker)
    monkeypatch.chdir(project)

    asyncio.run(EnhancedSQLiteSession.record_session_cwd("cwd-restore-session", str(target)))

    restored = asyncio.run(restore_session_cwd("cwd-restore-session"))

    assert restored == str(target)
    import os

    assert os.getcwd() == str(target.resolve()) or os.getcwd() == str(target)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["event"] == "CwdChanged"
    assert payload["cwd"] == str(target)
    assert payload["old_cwd"].endswith("project")


def test_restore_session_cwd_noop_when_already_there(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    marker = tmp_path / "cwd-changed.json"
    _write_cwd_hook(project, marker)
    monkeypatch.chdir(project)

    asyncio.run(EnhancedSQLiteSession.record_session_cwd("cwd-same-session", str(project)))

    assert asyncio.run(restore_session_cwd("cwd-same-session")) is None
    assert not marker.exists()


def test_restore_session_cwd_noop_for_missing_or_invalid_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    marker = tmp_path / "cwd-changed.json"
    _write_cwd_hook(project, marker)
    monkeypatch.chdir(project)

    # No recorded cwd at all
    assert asyncio.run(restore_session_cwd("cwd-unknown-session")) is None

    # Recorded cwd no longer exists on disk
    gone = tmp_path / "gone"
    asyncio.run(EnhancedSQLiteSession.record_session_cwd("cwd-gone-session", str(gone)))
    assert asyncio.run(restore_session_cwd("cwd-gone-session")) is None
    assert not marker.exists()


def test_restore_session_cwd_registers_hook_watch_paths(tmp_path, monkeypatch):
    from koder_agent.harness.hooks.runtime import poll_file_change_hooks

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
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
                                    "command": (
                                        'python -c "print(\'{\\"hookSpecificOutput\\":'
                                        f'{{\\"watchPaths\\":[\\"{watched}\\"]}}}}\')"'
                                    ),
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
                                    "command": (
                                        'python -c "import sys, pathlib; '
                                        f"pathlib.Path(r'{marker}').write_text(sys.stdin.read())\""
                                    ),
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

    asyncio.run(EnhancedSQLiteSession.record_session_cwd("cwd-watch-session", str(target)))
    assert asyncio.run(restore_session_cwd("cwd-watch-session")) == str(target)

    watched.write_text("two", encoding="utf-8")
    fired = poll_file_change_hooks(project)

    assert fired == 1
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "FileChanged"
