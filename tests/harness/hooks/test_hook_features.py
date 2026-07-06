"""Tests for hook features: stop_hook_active, once, disableAllHooks,
command timeout, output cap, KODER_ENV_FILE expansion, deduplication, PermissionDenied."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.hooks.runtime import (  # noqa: E402
    _MAX_HOOK_OUTPUT_CHARS,
    _once_fired,
    dispatch_command_hooks,
)


def _write_settings(project: Path, hooks_config: dict) -> None:
    (project / ".koder").mkdir(parents=True, exist_ok=True)
    (project / ".koder" / "settings.json").write_text(json.dumps(hooks_config), encoding="utf-8")


# ---- stop_hook_active ----


def test_stop_hook_payload_includes_stop_hook_active_field(tmp_path, monkeypatch):
    """Stop hook payload must contain stop_hook_active so hooks can break infinite loops."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "stop.json"
    _write_settings(
        project,
        {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                            }
                        ]
                    }
                ]
            }
        },
    )
    monkeypatch.chdir(project)
    dispatch_command_hooks(
        cwd=project,
        event_name="Stop",
        payload={
            "event": "Stop",
            "agent_type": "main",
            "last_assistant_message": "done",
            "stop_hook_active": False,
        },
    )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert "stop_hook_active" in payload
    assert payload["stop_hook_active"] is False


# ---- once: true ----


def test_once_true_hook_fires_only_once(tmp_path, monkeypatch):
    """A hook with once:true should fire on first dispatch then be skipped."""
    _once_fired.clear()
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    counter = tmp_path / "counter"
    counter.write_text("0", encoding="utf-8")
    _write_settings(
        project,
        {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import pathlib; p = pathlib.Path(r'{counter}'); p.write_text(str(int(p.read_text()) + 1))\"",
                                "once": True,
                            }
                        ]
                    }
                ]
            }
        },
    )
    monkeypatch.chdir(project)

    dispatch_command_hooks(
        cwd=project,
        event_name="SessionStart",
        payload={"event": "SessionStart", "source": "startup"},
    )
    assert int(counter.read_text()) == 1

    dispatch_command_hooks(
        cwd=project,
        event_name="SessionStart",
        payload={"event": "SessionStart", "source": "startup"},
    )
    assert int(counter.read_text()) == 1  # still 1, second dispatch was skipped
    _once_fired.clear()


# ---- disableAllHooks ----


def test_disable_all_hooks_suppresses_dispatch(tmp_path, monkeypatch):
    """disableAllHooks: true in settings should prevent all hooks from running."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "should-not-exist"
    _write_settings(
        project,
        {
            "disableAllHooks": True,
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import pathlib; pathlib.Path(r'{marker}').write_text('fired')\"",
                            }
                        ]
                    }
                ]
            },
        },
    )
    monkeypatch.chdir(project)

    result = dispatch_command_hooks(
        cwd=project,
        event_name="SessionStart",
        payload={"event": "SessionStart", "source": "startup"},
    )
    assert result.matched_hooks == 0
    assert not marker.exists()


# ---- command timeout ----


def test_command_hook_timeout_prevents_hang(tmp_path, monkeypatch):
    """A command hook with a short timeout should not hang forever."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    _write_settings(
        project,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'python -c "import time; time.sleep(30)"',
                                "timeout": 1,
                            }
                        ]
                    }
                ]
            }
        },
    )
    monkeypatch.chdir(project)

    result = dispatch_command_hooks(
        cwd=project,
        event_name="PreToolUse",
        match_value="Edit",
        payload={"event": "PreToolUse", "tool_name": "Edit", "tool_input": {}},
    )
    # Should not block forever; timeout causes non-blocking error (exit 1)
    assert result.matched_hooks == 1
    assert not result.blocked


# ---- output cap ----


def test_hook_output_is_capped_at_10000_chars(tmp_path, monkeypatch):
    """Hook additionalContext exceeding 10000 chars should be truncated."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    big_context = "x" * 15_000
    _write_settings(
        project,
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f'python -c "print(\'{{\\"hookSpecificOutput\\":{{\\"additionalContext\\":\\"{big_context}\\"}}}}\')"',
                            }
                        ]
                    }
                ]
            }
        },
    )
    monkeypatch.chdir(project)

    result = dispatch_command_hooks(
        cwd=project,
        event_name="PostToolUse",
        match_value="Edit",
        payload={"event": "PostToolUse", "tool_name": "Edit", "tool_input": {}, "result": "ok"},
    )
    assert result.additional_context is not None
    assert len(result.additional_context) <= _MAX_HOOK_OUTPUT_CHARS + 100  # some slack for suffix
    assert "truncated" in result.additional_context


# ---- KODER_ENV_FILE for CwdChanged and FileChanged ----


def test_cwd_changed_hook_receives_koder_env_file(tmp_path, monkeypatch):
    """CwdChanged hooks should receive KODER_ENV_FILE in their environment."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "env-file.txt"
    _write_settings(
        project,
        {
            "hooks": {
                "CwdChanged": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import os, pathlib; pathlib.Path(r'{marker}').write_text(os.environ.get('KODER_ENV_FILE', 'MISSING'))\"",
                            }
                        ]
                    }
                ]
            }
        },
    )
    monkeypatch.chdir(project)

    dispatch_command_hooks(
        cwd=project,
        event_name="CwdChanged",
        payload={
            "event": "CwdChanged",
            "session_id": "test-session",
            "new_cwd": str(tmp_path),
        },
    )
    content = marker.read_text(encoding="utf-8")
    assert content != "MISSING"
    assert "test-session" in content


def test_file_changed_hook_receives_koder_env_file(tmp_path, monkeypatch):
    """FileChanged hooks should receive KODER_ENV_FILE in their environment."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "env-file-fc.txt"
    _write_settings(
        project,
        {
            "hooks": {
                "FileChanged": [
                    {
                        "matcher": "test.txt",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import os, pathlib; pathlib.Path(r'{marker}').write_text(os.environ.get('KODER_ENV_FILE', 'MISSING'))\"",
                            }
                        ],
                    }
                ]
            }
        },
    )
    monkeypatch.chdir(project)

    dispatch_command_hooks(
        cwd=project,
        event_name="FileChanged",
        match_value="test.txt",
        payload={
            "event": "FileChanged",
            "session_id": "test-fc-session",
            "file_path": str(project / "test.txt"),
        },
    )
    content = marker.read_text(encoding="utf-8")
    assert content != "MISSING"
    assert "test-fc-session" in content


# ---- hook deduplication ----


def test_identical_hooks_are_deduplicated(tmp_path, monkeypatch):
    """Identical hook commands in the same dispatch should fire only once."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    counter = tmp_path / "dedup-counter"
    counter.write_text("0", encoding="utf-8")
    _write_settings(
        project,
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import pathlib; p = pathlib.Path(r'{counter}'); p.write_text(str(int(p.read_text()) + 1))\"",
                            }
                        ]
                    },
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import pathlib; p = pathlib.Path(r'{counter}'); p.write_text(str(int(p.read_text()) + 1))\"",
                            }
                        ]
                    },
                ]
            }
        },
    )
    monkeypatch.chdir(project)

    result = dispatch_command_hooks(
        cwd=project,
        event_name="PostToolUse",
        match_value="Edit",
        payload={"event": "PostToolUse", "tool_name": "Edit", "tool_input": {}, "result": "ok"},
    )
    assert result.matched_hooks == 1  # deduplicated to one
    assert int(counter.read_text()) == 1


# ---- PermissionDenied event ----


def test_permission_denied_hook_fires_when_tool_is_denied(tmp_path, monkeypatch):
    """PermissionDenied hook should fire when the permission service denies a tool call."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "permission-denied.json"
    _write_settings(
        project,
        {
            "hooks": {
                "PermissionDenied": [
                    {
                        "matcher": "run_shell",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                            }
                        ],
                    }
                ]
            }
        },
    )
    monkeypatch.chdir(project)

    # Directly test the dispatch function (PermissionDenied is dispatched from registry)
    dispatch_command_hooks(
        cwd=project,
        event_name="PermissionDenied",
        match_value="run_shell",
        payload={
            "event": "PermissionDenied",
            "tool_name": "run_shell",
            "tool_input": {"command": "rm -rf /"},
            "reason": "Dangerous command",
        },
    )
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["event"] == "PermissionDenied"
    assert payload["tool_name"] == "run_shell"
    assert payload["reason"] == "Dangerous command"
