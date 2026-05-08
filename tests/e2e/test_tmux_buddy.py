from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import textwrap
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


def test_tmux_buddy_runs_through_uv_interactive_session(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    session = f"koder-buddy-{uuid.uuid4().hex[:8]}"
    script = textwrap.dedent("""
        import asyncio
        import koder_agent.cli as cli
        raise SystemExit(asyncio.run(cli.main()))
        """).strip()
    launch = " ".join(
        [
            "cd",
            shlex.quote(str(PROJECT_ROOT)),
            "&&",
            "HOME=" + shlex.quote(str(home)),
            "USER=tmux-buddy",
            "uv",
            "run",
            "python",
            "-c",
            shlex.quote(script),
        ]
    )
    try:
        _tmux("new-session", "-d", "-s", session, launch)
        _wait_for_prompt(session)
        _send(session, f"/teleport {repo}")
        _wait_for_output(session, "cwd:")
        _send(session, "/buddy")
        output = _wait_for_output(session, "buddy: hatched")
        assert "buddy: hatched" in output
        assert "species:" in output
        match = re.search(r"name: (\S+)", output)
        assert match is not None
        name = match.group(1)
        _wait_for_prompt(session)
        _send(session, "/buddy")
        pet_output = _wait_for_output(session, "reaction:")
        reaction_match = re.search(r"reaction: (.+)", pet_output)
        assert reaction_match is not None
        reaction_text = reaction_match.group(1).strip()
        _wait_for_prompt(session)
        time.sleep(0.6)
        pet_prompt = _capture(session)
        pet_trailing_lines = "\n".join(pet_prompt.splitlines()[-12:])
        assert '"' in pet_trailing_lines or reaction_text[:12] in pet_trailing_lines
        _send(session, "/status")
        _wait_for_prompt(session)
        time.sleep(0.6)
        status_output = _capture(session)
        trailing_lines = "\n".join(status_output.splitlines()[-12:])
        assert "buddy: hatched" not in trailing_lines
        assert name in trailing_lines
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
