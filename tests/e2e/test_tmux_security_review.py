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


def test_tmux_security_review_short_circuits_for_clean_repo(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked = repo / "tracked.txt"
    tracked.write_text("seed\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    session = f"koder-security-review-{uuid.uuid4().hex[:8]}"
    launch = " ".join(
        [
            "cd",
            shlex.quote(str(repo)),
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
        _send(session, "/security-review")
        output = _wait_for_output(session, "security-review: no pending changes to review.")
        assert "security-review: no pending changes to review." in output
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
