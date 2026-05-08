from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )


def _capture(session: str) -> str:
    return _tmux("capture-pane", "-pS", "-400", "-t", session).stdout


def _send(session: str, command: str) -> str:
    _tmux("send-keys", "-t", session, command, "C-m")
    time.sleep(1.0)
    return _capture(session)


def _wait_for_output(session: str, expected: str, *, timeout: float = 12.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        if expected in last_output:
            return last_output
        time.sleep(0.5)
    return last_output


def _wait_for_prompt(session: str, *, timeout: float = 12.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        trailing = "\n".join(last_output.splitlines()[-8:])
        if "⚡ Koder" in trailing and "│>" in trailing:
            return last_output
        time.sleep(0.5)
    return last_output


def test_tmux_statusline_command_imports_shell_prompt(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshrc").write_text('export PS1="project:\\W\\$ "\n', encoding="utf-8")

    session = f"koder-statusline-{uuid.uuid4().hex[:8]}"
    try:
        _tmux(
            "new-session",
            "-d",
            "-s",
            session,
            f"cd {PROJECT_ROOT} && HOME={home} uv run koder",
        )
        _wait_for_prompt(session, timeout=20.0)
        _send(session, "/statusline")
        configured_output = _wait_for_output(session, "statusline: configured from", timeout=20.0)
        assert "statusline: configured from" in configured_output

        prompt_output = _wait_for_output(session, "project:koder", timeout=20.0)
        assert "project:koder" in prompt_output

        saved = json.loads((home / ".koder" / "settings.json").read_text(encoding="utf-8"))
        assert saved["statusLine"]["type"] == "command"
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
