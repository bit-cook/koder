from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UV = shutil.which("uv") or "uv"


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


def test_tmux_pr_comments_uses_local_gh_backed_markdown_contract(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    shutil.copyfile(PROJECT_ROOT / "tests" / "fixtures" / "fake_gh_pr_comments.sh", fake_gh)
    fake_gh.chmod(0o755)

    session = f"koder-pr-comments-{uuid.uuid4().hex[:8]}"
    launch = " ".join(
        [
            "cd",
            shlex.quote(str(PROJECT_ROOT)),
            "&&",
            "PATH=" + shlex.quote(f"{fake_bin}:{os.environ.get('PATH', '')}"),
            "HOME=" + shlex.quote(str(home)),
            "PYTHONPATH=" + shlex.quote(str(PROJECT_ROOT)),
            "KODER_MODEL=gpt-4.1",
            shlex.quote(UV),
            "--project",
            shlex.quote(str(PROJECT_ROOT)),
            "run",
            "--no-sync",
            "koder",
            "--teammate-mode",
            "tmux",
        ]
    )
    try:
        _tmux("new-session", "-d", "-s", session, launch)
        _wait_for_prompt(session)
        _send(session, "/pr-comments")
        output = _wait_for_output(session, "## Comments")
        assert "## Comments" in output
        assert "@alice" in output
        assert "src/app.py#42" in output
        assert "@carol" in output
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
