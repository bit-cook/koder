from __future__ import annotations

import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_LINE_RE = re.compile(r"^│>\s?(.*?)\s*│$")


def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )


def _launch(session: str, *, home: Path) -> None:
    (home / ".koder").mkdir(parents=True, exist_ok=True)
    _tmux(
        "new-session",
        "-d",
        "-s",
        session,
        f"export HOME={home} && cd {PROJECT_ROOT} && uv run koder",
    )


def _capture(session: str) -> str:
    return _tmux("capture-pane", "-pS", "-200", "-t", session).stdout


def _send(session: str, command: str) -> str:
    _tmux("send-keys", "-t", session, command, "C-m")
    time.sleep(1.0)
    return _capture(session)


def _send_keys(session: str, *keys: str, wait: float = 1.0) -> str:
    _tmux("send-keys", "-t", session, *keys)
    time.sleep(wait)
    return _capture(session)


def _wait_for_output(session: str, expected: str, *, timeout: float = 8.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        if expected in last_output:
            return last_output
        time.sleep(0.5)
    return last_output


def _wait_for(session: str, predicate, *, timeout: float = 8.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        if predicate(last_output):
            return last_output
        time.sleep(0.5)
    return last_output


def _wait_for_prompt(session: str, *, timeout: float = 10.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        trailing_lines = "\n".join(last_output.splitlines()[-8:])
        if "⚡ Koder" in trailing_lines and "│>" in trailing_lines:
            return last_output
        time.sleep(0.5)
    return last_output


def _latest_prompt_text(output: str) -> str:
    for line in reversed(output.splitlines()):
        match = _PROMPT_LINE_RE.match(line)
        if match:
            return match.group(1).rstrip()
    return ""


def test_tmux_interactive_history_up_arrow_resets_after_clear(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    session = f"koder-e2e-history-{uuid.uuid4().hex[:8]}"
    home = tmp_path / "home"
    home.mkdir()

    try:
        _launch(session, home=home)
        _wait_for_prompt(session)

        _send(session, "!printf up_history_seed")
        output = _wait_for(
            session,
            lambda text: "Shell Mode" in text and "up_history_seed" in text,
        )
        assert "Shell Mode" in output

        output = _send_keys(session, "Up")
        assert _latest_prompt_text(output) == "!printf up_history_seed"

        _send_keys(session, "C-d")
        _send(session, "/clear")
        output = _wait_for_output(session, "Switched to session:")
        assert _latest_prompt_text(output) == ""

        output = _send_keys(session, "Up")
        assert _latest_prompt_text(output) == ""
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )


def test_tmux_interactive_ctrl_r_replays_previous_command(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    session = f"koder-e2e-search-{uuid.uuid4().hex[:8]}"
    home = tmp_path / "home"
    home.mkdir()

    try:
        _launch(session, home=home)
        _wait_for_prompt(session)

        _send(session, "!printf ctrlr_seed")
        output = _wait_for(
            session,
            lambda text: "Shell Mode" in text and "ctrlr_seed" in text,
        )
        assert "Shell Mode" in output
        baseline_shell_mode_count = output.count("Shell Mode")

        _send_keys(session, "C-r")
        output = _send_keys(session, "seed")
        assert "I-search backward: seed" in output
        assert _latest_prompt_text(output) == "!printf ctrlr_seed"

        _send_keys(session, "Enter")
        output = _wait_for(
            session,
            lambda text: text.count("Shell Mode") >= baseline_shell_mode_count + 1,
        )
        assert output.count("Shell Mode") >= baseline_shell_mode_count + 1
        assert "ctrlr_seed" in output
        assert _latest_prompt_text(output) == ""
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", session],
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
