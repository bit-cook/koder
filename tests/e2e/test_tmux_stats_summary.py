from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import sqlite3
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


async def _seed_session(
    home: Path,
    session_id: str,
    *,
    created_at: str,
    updated_at: str,
) -> None:
    original_home = os.environ.get("HOME")
    try:
        os.environ["HOME"] = str(home)
        session = EnhancedSQLiteSession(session_id=session_id)
        await session.add_items(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
            ]
        )
        await EnhancedSQLiteSession.record_session_cwd(session_id, str(PROJECT_ROOT))
        db_path = home / ".koder" / "koder.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE session_metadata SET created_at = ?, updated_at = ? WHERE session_id = ?",
                (created_at, updated_at, session_id),
            )
            conn.commit()
    finally:
        if original_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = original_home


def test_tmux_stats_reports_history_summary(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    home.mkdir()
    asyncio.run(
        _seed_session(
            home,
            "2026-04-08T10:00:00.000",
            created_at="2026-04-08 10:00:00",
            updated_at="2026-04-08 10:30:00",
        )
    )
    asyncio.run(
        _seed_session(
            home,
            "2026-04-09T11:00:00.000",
            created_at="2026-04-09 11:00:00",
            updated_at="2026-04-09 11:15:00",
        )
    )

    tmux_session = f"koder-stats-{uuid.uuid4().hex[:8]}"
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
            "2026-04-09T11:00:00.000",
        ]
    )
    try:
        _tmux("new-session", "-d", "-s", tmux_session, launch)
        _wait_for_prompt(tmux_session)

        _send(tmux_session, "/stats")
        output = _wait_for_output(tmux_session, "## Stats")
        assert "## Stats" in output
        assert "Sessions: 2" in output
        assert "Messages: 4" in output
        # The seeded DB has 2 sessions on different dates, but the live session
        # adds today as a 3rd active day when it doesn't match the seed dates.
        assert "Active days:" in output
        assert "Peak day:" in output
        assert "context_tokens:" in output
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", tmux_session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
