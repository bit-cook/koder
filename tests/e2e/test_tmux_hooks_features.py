"""Tmux-driven interactive E2E tests for hooks features.

Each test launches ``uv run koder`` inside tmux with HOME pointing to
a temp directory where hooks are pre-configured, then interacts with
the live session to verify the hooks fire as documented.
"""

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
    return _tmux("capture-pane", "-pS", "-500", "-t", session).stdout


def _send(session: str, keys: str) -> None:
    _tmux("send-keys", "-t", session, keys, "C-m")


def _wait_for(session: str, expected: str, *, timeout: float = 12.0) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = _capture(session)
        if expected in last:
            return last
        time.sleep(0.5)
    return last


def _wait_prompt(session: str, *, timeout: float = 15.0) -> str:
    return _wait_for(session, "⚡ Koder", timeout=timeout)


def _skip_no_tmux():
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")


def _write_user_settings(home: Path, config: dict) -> None:
    """Write settings.json at user scope (~/.koder/settings.json)."""
    koder_dir = home / ".koder"
    koder_dir.mkdir(parents=True, exist_ok=True)
    (koder_dir / "settings.json").write_text(json.dumps(config), encoding="utf-8")


def _launch(session: str, home: Path) -> None:
    """Launch koder in tmux with HOME set to *home*."""
    _tmux(
        "new-session",
        "-d",
        "-s",
        session,
        f"export HOME={home} && cd {PROJECT_ROOT} && uv run koder",
    )
    prompt_output = _wait_prompt(session)
    assert "⚡ Koder" in prompt_output, f"koder did not start: {prompt_output!r}"


# ---------------------------------------------------------------------------
# Test: /hooks shows configured hooks interactively
# ---------------------------------------------------------------------------


def test_tmux_hooks_shows_configured_hooks(tmp_path):
    """Interactive /hooks displays configured hook events."""
    _skip_no_tmux()

    _write_user_settings(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "echo guard"}],
                    }
                ]
            }
        },
    )

    session = f"koder-hooks-{uuid.uuid4().hex[:6]}"
    try:
        _launch(session, tmp_path)
        _send(session, "/hooks")
        output = _wait_for(session, "hooks:")
        assert "PreToolUse" in output
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            text=True,
            capture_output=True,
        )


# ---------------------------------------------------------------------------
# Test: SessionStart hook fires when koder starts
# ---------------------------------------------------------------------------


def test_tmux_session_start_hook_fires(tmp_path):
    """A SessionStart hook should fire and write a marker file."""
    _skip_no_tmux()

    marker = tmp_path / "session-started.txt"
    _write_user_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import pathlib; pathlib.Path(r'{marker}').write_text('started')\"",
                            }
                        ],
                    }
                ]
            }
        },
    )

    session = f"koder-start-{uuid.uuid4().hex[:6]}"
    try:
        _launch(session, tmp_path)
        # Give hook a moment to complete after prompt appears
        time.sleep(1.0)
        assert marker.exists(), "SessionStart hook did not fire"
        assert marker.read_text(encoding="utf-8") == "started"
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            text=True,
            capture_output=True,
        )


# ---------------------------------------------------------------------------
# Test: CwdChanged hook fires on /teleport
# ---------------------------------------------------------------------------


def test_tmux_cwd_changed_hook_fires_on_teleport(tmp_path):
    """CwdChanged hook fires when user runs /teleport."""
    _skip_no_tmux()

    target = tmp_path / "target"
    target.mkdir()
    marker = tmp_path / "cwd-changed.txt"
    _write_user_settings(
        tmp_path,
        {
            "hooks": {
                "CwdChanged": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import pathlib; pathlib.Path(r'{marker}').write_text('changed')\"",
                            }
                        ]
                    }
                ]
            }
        },
    )

    session = f"koder-cwd-{uuid.uuid4().hex[:6]}"
    try:
        _launch(session, tmp_path)
        _send(session, f"/teleport {target}")
        _wait_for(session, str(target.resolve()), timeout=8.0)
        time.sleep(1.0)

        assert marker.exists(), "CwdChanged hook did not fire on /teleport"
        assert marker.read_text(encoding="utf-8") == "changed"
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            text=True,
            capture_output=True,
        )


# ---------------------------------------------------------------------------
# Test: once:true hook fires and marker proves execution
# ---------------------------------------------------------------------------


def test_tmux_once_hook_fires_on_startup(tmp_path):
    """A once:true SessionStart hook should fire and create marker."""
    _skip_no_tmux()

    counter = tmp_path / "once-counter"
    counter.write_text("0", encoding="utf-8")
    _write_user_settings(
        tmp_path,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup",
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    f"python -c \"import pathlib; p = pathlib.Path(r'{counter}');"
                                    ' p.write_text(str(int(p.read_text()) + 1))"'
                                ),
                                "once": True,
                            }
                        ],
                    }
                ]
            }
        },
    )

    session = f"koder-once-{uuid.uuid4().hex[:6]}"
    try:
        _launch(session, tmp_path)
        time.sleep(1.0)
        assert int(counter.read_text()) == 1, "once:true hook should fire exactly once"
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            text=True,
            capture_output=True,
        )
