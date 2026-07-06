from __future__ import annotations

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


def test_tmux_advisor_review_runs_through_uv_interactive_session(tmp_path):
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
    tracked = repo / "auth.py"
    tracked.write_text("def auth():\n    return 'ok'\n", encoding="utf-8")
    subprocess.run(["git", "add", "auth.py"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked.write_text("def auth(user_input):\n    return user_input\n", encoding="utf-8")

    session = f"koder-advisor-{uuid.uuid4().hex[:8]}"
    script = textwrap.dedent("""
        import asyncio
        import koder_agent.harness.commands.advisor as advisor
        import koder_agent.cli as cli

        async def fake_completion(messages, model=None):
            return "# Advisor Review\\n\\n## Assessment\\n- Add an auth regression test."

        advisor.llm_completion = fake_completion
        raise SystemExit(asyncio.run(cli.main()))
        """).strip()
    launch = " ".join(
        [
            "cd",
            shlex.quote(str(repo)),
            "&&",
            "HOME=" + shlex.quote(str(home)),
            "uv",
            "--project",
            shlex.quote(str(PROJECT_ROOT)),
            "run",
            "--no-sync",
            "python",
            "-c",
            shlex.quote(script),
        ]
    )
    try:
        _tmux("new-session", "-d", "-s", session, launch)
        _wait_for_prompt(session)
        _send(session, "/advisor focus on auth regressions")
        output = _wait_for_output(session, "# Advisor Review")
        assert "# Advisor Review" in output
        assert "Add an auth regression test." in output
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
