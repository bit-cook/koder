from __future__ import annotations

import asyncio
import json
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


async def _seed_session(home: Path, session_id: str, tracked_file: Path) -> None:
    original_home = os.environ.get("HOME")
    try:
        os.environ["HOME"] = str(home)
        session = EnhancedSQLiteSession(session_id=session_id)
        await session.add_items(
            [
                {
                    "type": "function_call",
                    "name": "read_file",
                    "arguments": json.dumps({"path": str(tracked_file)}),
                }
            ]
        )
        await EnhancedSQLiteSession.record_session_cwd(session_id, str(PROJECT_ROOT))
    finally:
        if original_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = original_home


def test_tmux_files_lists_session_context_files(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    home.mkdir()
    tracked = PROJECT_ROOT / "AGENTS.md"
    session_id = "2026-04-09T12:00:00.000"
    asyncio.run(_seed_session(home, session_id, tracked))

    tmux_session = f"koder-files-{uuid.uuid4().hex[:8]}"
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

        _send(tmux_session, "/files")
        output = _wait_for_output(tmux_session, "Files in context:")
        assert "Files in context:" in output
        assert "AGENTS.md" in output
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", tmux_session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
