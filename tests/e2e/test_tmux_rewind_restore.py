from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from koder_agent.core.session import EnhancedSQLiteSession

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


def _wait_for_output(session: str, expected: str, *, timeout: float = 20.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        if expected in last_output:
            return last_output
        time.sleep(0.5)
    return last_output


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


def _send(session: str, command: str) -> str:
    _tmux("send-keys", "-t", session, command, "C-m")
    time.sleep(1.0)
    return _capture(session)


async def _seed_session(home: Path, session_id: str) -> None:
    original_home = os.environ.get("HOME")
    try:
        os.environ["HOME"] = str(home)
        session = EnhancedSQLiteSession(session_id=session_id)
        await session.add_items(
            [
                {"role": "user", "content": "first prompt"},
                {"role": "assistant", "content": "first reply"},
                {"role": "user", "content": "second prompt"},
                {"role": "assistant", "content": "second reply"},
            ]
        )
    finally:
        if original_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = original_home


def test_tmux_rewind_restores_selected_prompt_into_input(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    home.mkdir()
    session_id = "2026-04-09T12:00:00.000"
    asyncio.run(_seed_session(home, session_id))

    tmux_session = f"koder-rewind-{uuid.uuid4().hex[:8]}"
    launch = " ".join(
        [
            "cd",
            shlex.quote(str(PROJECT_ROOT)),
            "&&",
            "HOME=" + shlex.quote(str(home)),
            "uv",
            "run",
            "koder",
            "--session",
            shlex.quote(session_id),
        ]
    )

    try:
        _tmux("new-session", "-d", "-s", tmux_session, launch)
        _wait_for_prompt(tmux_session)

        _send(tmux_session, "/rewind")
        listing_output = _wait_for_output(tmux_session, "Rewind targets")
        assert "1. second prompt" in listing_output

        _send(tmux_session, "/rewind 1")
        prompt_output = _wait_for_output(tmux_session, "second prompt")
        trailing_lines = "\n".join(prompt_output.splitlines()[-8:])
        assert "second prompt" in trailing_lines

        original_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = str(home)
            restored_items = asyncio.run(EnhancedSQLiteSession(session_id=session_id).get_items())
        finally:
            if original_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = original_home
        assert restored_items == [
            {"role": "user", "content": "first prompt"},
            {"role": "assistant", "content": "first reply"},
        ]
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", tmux_session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
