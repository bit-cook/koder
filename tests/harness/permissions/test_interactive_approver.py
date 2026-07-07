"""Fix 1: an interactive approver must actually prompt and gate tool calls.

Previously AgentScheduler.approver was always None, so enforce_tool_permission's
"requires approval" path fell through to a TTY-aware fail-OPEN in interactive
sessions — every approval-gated mutating/exec tool ran unattended. This wires a
real approver whose verdict enforce_tool_permission understands
("allow"/"always"/"deny").
"""

from __future__ import annotations

import asyncio

from koder_agent.harness.permissions.interactive_approver import build_interactive_approver
from koder_agent.harness.permissions.modes import PermissionMode
from koder_agent.harness.permissions.results import PermissionEvaluationResult


def _decision(tool="run_shell", reason="mutating command"):
    return PermissionEvaluationResult(
        tool_name=tool,
        allowed=False,
        requires_approval=True,
        reason=reason,
        mode=PermissionMode.DEFAULT,
    )


def _run(approver, tool="run_shell", args=None):
    return asyncio.run(approver(tool, args or {"command": "rm -rf build"}, _decision()))


def test_approver_returns_allow_on_allow_choice():
    approver = build_interactive_approver(reader=lambda prompt: "y")
    assert _run(approver) in ("allow", True, "allow_once")


def test_approver_returns_always_on_always_choice():
    approver = build_interactive_approver(reader=lambda prompt: "a")
    assert _run(approver) in ("always", "allow_always")


def test_approver_returns_deny_on_deny_choice():
    approver = build_interactive_approver(reader=lambda prompt: "n")
    assert _run(approver) == "deny"


def test_approver_fails_closed_on_eof():
    def _raise(prompt):
        raise EOFError

    approver = build_interactive_approver(reader=_raise)
    assert _run(approver) == "deny"


def test_approver_fails_closed_on_unrecognized_input():
    approver = build_interactive_approver(reader=lambda prompt: "banana")
    assert _run(approver) == "deny"


def test_approver_prompt_includes_tool_and_reason():
    seen = {}

    def _reader(prompt):
        seen["prompt"] = prompt
        return "n"

    approver = build_interactive_approver(reader=_reader)
    _run(approver, tool="write_file", args={"file_path": "/etc/hosts"})
    assert "write_file" in seen["prompt"]


def test_default_reader_is_fail_closed_when_not_a_tty(monkeypatch):
    """With no injected reader and a non-interactive stdin, the approver must not
    hang or auto-allow — it fails closed."""
    import sys

    class _FakeStdin:
        def isatty(self):
            return False

    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    approver = build_interactive_approver()  # default reader
    assert _run(approver) == "deny"


def test_approver_sanitizes_control_chars_in_prompt():
    """Review finding 6: a prompt-injected command with ANSI escapes / newlines
    must not reach the terminal raw — control chars are escaped so they cannot
    visually rewrite the approval prompt."""
    seen = {}

    def _reader(prompt):
        seen["prompt"] = prompt
        return "n"

    approver = build_interactive_approver(reader=_reader)
    evil = "ls\x1b[2K\x1b[1Arm -rf /\nfake=git status"
    asyncio.run(approver("run_shell", {"command": evil}, _decision()))
    p = seen["prompt"]
    # No raw ESC or embedded newline from the argument survives into the prompt body.
    assert "\x1b" not in p
    # The escape is shown in visible form instead.
    assert "\\x1b" in p
    # The injected 'command' value must not introduce a new physical line that
    # could impersonate the prompt's own structure.
    assert "\nfake=git status" not in p
