from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path

from koder_agent.harness.version_info import resolve_runtime_version

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


def _wait_for_output(session: str, expected: str, *, timeout: float = 12.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        if expected in last_output:
            return last_output
        time.sleep(0.5)
    return last_output


def _wait_for_prompt(session: str, *, timeout: float = 12.0) -> str:
    deadline = time.time() + timeout
    last_output = ""
    while time.time() < deadline:
        last_output = _capture(session)
        trailing = "\n".join(last_output.splitlines()[-8:])
        if "⚡ Koder" in trailing and "│>" in trailing:
            return last_output
        time.sleep(0.5)
    return last_output


def test_tmux_release_notes_command_uses_cached_changelog(tmp_path):
    if shutil.which("tmux") is None:
        import pytest

        pytest.skip("tmux is not available")

    home = tmp_path / "home"
    cache_path = home / ".koder" / "cache" / "changelog.md"
    config_path = home / ".koder" / "config.yaml"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        "\n".join(
            [
                "# Changelog",
                "",
                "## 0.4.13 - 2026-04-09",
                "- Added configurable statusline setup",
                "",
                "## 0.4.12 - 2026-04-01",
                "- Added performance improvements",
            ]
        ),
        encoding="utf-8",
    )
    config_path.write_text("harness:\n  last_release_notes_seen: 0.4.12\n", encoding="utf-8")

    session = f"koder-release-notes-{uuid.uuid4().hex[:8]}"
    try:
        _tmux(
            "new-session",
            "-d",
            "-s",
            session,
            f"cd {PROJECT_ROOT} && HOME={home} uv run koder",
        )
        _wait_for_prompt(session, timeout=20.0)
        _send(session, "/release-notes")
        output = _wait_for_output(session, "Version 0.4.13:", timeout=20.0)
        assert "Version 0.4.13:" in output
        assert "Added configurable statusline setup" in output
        saved = config_path.read_text(encoding="utf-8")
        assert f"last_release_notes_seen: {resolve_runtime_version()}" in saved
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
