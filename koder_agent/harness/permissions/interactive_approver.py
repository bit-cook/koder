"""Interactive tool-approval prompt for the main agent session.

Wires the missing piece behind ``enforce_tool_permission``'s "requires approval"
branch: without an approver, that branch fell through to a TTY-aware fail-OPEN, so
every approval-gated mutating/exec tool ran unattended in an interactive session.

``build_interactive_approver`` returns an async callable matching the
``Approver`` contract expected by ``koder_agent.tools.permission_context``:

    approver(tool_name: str, arguments: dict, decision) -> "allow" | "always" | "deny"

The verdict strings map onto the sets ``enforce_tool_permission`` recognizes
(``_ALLOW_ONCE_RESULTS`` / ``_ALLOW_ALWAYS_RESULTS``); ``"deny"`` blocks the call.
The prompt reader is injectable so the decision logic is unit-testable without a
live terminal, and every failure mode (EOF, unrecognized input, reader error)
fails CLOSED (deny) rather than silently allowing.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Optional

# Verdict strings understood by enforce_tool_permission.
_ALLOW = "allow"
_ALWAYS = "always"
_DENY = "deny"

# Single-key answers -> verdict.
_ANSWER_MAP = {
    "y": _ALLOW,
    "yes": _ALLOW,
    "allow": _ALLOW,
    "o": _ALLOW,  # "once"
    "once": _ALLOW,
    "a": _ALWAYS,
    "always": _ALWAYS,
    "n": _DENY,
    "no": _DENY,
    "d": _DENY,
    "deny": _DENY,
}

Reader = Callable[[str], str]


def _default_reader(prompt: str) -> str:
    """Read a line from the real terminal, failing closed on non-TTY or error."""
    try:
        if not sys.stdin.isatty():
            return ""
        return input(prompt)
    except Exception:
        return ""


def _sanitize(text: str) -> str:
    """Escape control characters so a malicious argument cannot spoof the prompt.

    A prompt-injected command/reason containing ANSI escapes, carriage returns,
    or newlines could otherwise visually rewrite the approval prompt (e.g. move
    the cursor up and overwrite the tool name) to trick the human into approving
    a disguised call. Render control chars (incl. ESC/CSI) as visible ``\\xNN``.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if code < 0x20 or code == 0x7F or code == 0x9B:
            out.append(f"\\x{code:02x}")
        else:
            out.append(ch)
    return "".join(out)


def _summarize_arguments(tool_name: str, arguments: dict) -> str:
    """Render the most decision-relevant argument for the prompt line."""
    if not isinstance(arguments, dict):
        return ""
    for key in ("command", "file_path", "path", "args", "url", "uri"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            shown = value if len(value) <= 200 else value[:197] + "..."
            return f"{key}={_sanitize(shown)}"
    return ""


def build_interactive_approver(reader: Optional[Reader] = None) -> Callable[..., Any]:
    """Build an async approver that prompts the user to allow/always/deny a call.

    Args:
        reader: Optional callable ``(prompt) -> answer`` used to obtain the user's
            choice. Injectable for tests; defaults to a fail-closed terminal reader.

    Returns:
        An async callable ``(tool_name, arguments, decision) -> str`` returning
        ``"allow"``, ``"always"``, or ``"deny"``.
    """
    read = reader or _default_reader

    async def _approver(tool_name: str, arguments: dict, decision: Any) -> str:
        reason = getattr(decision, "reason", "") or ""
        arg_summary = _summarize_arguments(tool_name, arguments)
        # tool_name comes from our own registry, but arguments/reason may echo
        # untrusted, prompt-injected content — sanitize control chars so they
        # can't rewrite the prompt the human reads.
        lines = [f"\nPermission required: {_sanitize(tool_name)}"]
        if arg_summary:
            lines.append(f"  {arg_summary}")
        if reason:
            lines.append(f"  reason: {_sanitize(reason)}")
        lines.append("  [y]allow once  [a]always allow  [n]deny > ")
        prompt = "\n".join(lines)

        try:
            # The default reader blocks on terminal input; run it off the event
            # loop so a pending prompt cannot freeze streaming, background agents,
            # channel consumers, or the ESC-cancel path (review findings 4/8). A
            # custom (test) reader is called directly to keep tests deterministic.
            if reader is None:
                import asyncio

                answer = await asyncio.to_thread(read, prompt)
            else:
                answer = read(prompt)
        except Exception:
            # Any reader failure (including EOFError) fails closed.
            return _DENY

        if not isinstance(answer, str):
            return _DENY
        return _ANSWER_MAP.get(answer.strip().lower(), _DENY)

    return _approver
