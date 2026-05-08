from __future__ import annotations

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


def test_tmux_agents_show_displays_custom_agent_metadata(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    agent_file = repo / ".koder" / "agents" / "reviewer.md"
    agent_file.parent.mkdir(parents=True, exist_ok=True)
    agent_file.write_text(
        "\n".join(
            [
                "---",
                "name: reviewer",
                "description: Reviews code carefully",
                "tools:",
                "  - Read",
                "  - Bash",
                "model: sonnet",
                "permissionMode: plan",
                "memory: user",
                "---",
                "You are a reviewer.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    session = f"koder-agents-{uuid.uuid4().hex[:8]}"
    launch = " ".join(
        [
            "cd",
            shlex.quote(str(PROJECT_ROOT)),
            "&&",
            "HOME=" + shlex.quote(str(home)),
            shlex.quote(UV),
            "run",
            "koder",
        ]
    )
    try:
        _tmux("new-session", "-d", "-s", session, launch)
        _wait_for_prompt(session)
        _send(session, f"/teleport {repo}")
        _wait_for_output(session, "cwd:")
        _send(session, "/agents show reviewer")
        output = _wait_for_output(session, "description: Reviews code carefully")
        assert "tools: Read, Bash" in output
        assert "model: sonnet" in output
        assert "permission_mode: plan" in output
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
