from __future__ import annotations

import shlex
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


def _wait_for_prompt(session: str, *, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        trailing_lines = "\n".join(last_output.splitlines()[-8:])
        if "⚡ Koder" in trailing_lines and "│>" in trailing_lines:
            return last_output
        time.sleep(0.5)
    return last_output


def _wait_for_output(session: str, expected: str, *, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        if expected in last_output:
            return last_output
        time.sleep(0.5)
    return last_output


def _send(session: str, command: str) -> str:
    _tmux("send-keys", "-t", session, command, "C-m")
    time.sleep(1.0)
    return _capture(session)


def test_tmux_doctor_reports_installation_and_ripgrep_status(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    home.mkdir()
    session = f"koder-doctor-{uuid.uuid4().hex[:8]}"
    launch = " ".join(
        [
            "cd",
            shlex.quote(str(PROJECT_ROOT)),
            "&&",
            "HOME=" + shlex.quote(str(home)),
            "uv",
            "run",
            "koder",
        ]
    )
    try:
        _tmux("new-session", "-d", "-s", session, launch)
        _wait_for_prompt(session)
        _send(session, "/doctor")
        output = _wait_for_output(session, "ripgrep_path:")
        assert "python:" in output
        assert "installation_type:" in output
        assert "invoked_binary:" in output
        assert "ripgrep_working:" in output
        assert "ripgrep_mode:" in output
        assert "ripgrep_path:" in output
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
