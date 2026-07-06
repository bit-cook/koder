"""Argument-level tool permission enforcement for the main agent chain.

The main agent's tools are plain SDK ``function_tool``s that the runner executes
directly — they never pass through ``harness/tools/registry.py``'s ``guarded_invoke``.
That left a gap: ``ApprovalHooks.on_tool_start`` only sees the tool *name* (not its
arguments), so the permission service could never make an argument-level decision for
the main chain. A shell command the policy would *deny* still ran, because the
"validation deferred until invocation" branch had no one to defer to.

This module closes that gap. The scheduler publishes the active
``PermissionService`` (and an optional interactive approver) into a context variable
right before invoking ``Runner.run`` / ``Runner.run_streamed``. Because the SDK runs
tools inside a task that copies the context captured at that call, the value is
visible inside each tool's ``on_invoke_tool``. The ``function_tool`` wrapper
(``tools/compat.py``) then calls :func:`enforce_tool_permission` with the *real*
arguments and blocks denied calls by returning an error string to the model (rather
than raising, so the model can adapt).

Reading a contextvar that was set *before* the run works fine; only mutations made
*inside* a tool fail to propagate back out (the SDK copies the context for tool
invocation). Enforcement only reads, so it is unaffected.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from koder_agent.harness.permissions.results import PermissionEvaluationResult
    from koder_agent.harness.permissions.service import PermissionService

logger = logging.getLogger(__name__)

# Tools whose *arguments* materially affect safety and therefore warrant an
# argument-level permission check. Read-only and metadata tools are intentionally
# excluded so the common path stays zero-overhead.
GUARDED_TOOLS: frozenset[str] = frozenset(
    {
        "run_shell",
        "run_powershell",
        "git_command",
        "write_file",
        "edit_file",
        "append_file",
    }
)

# Env flag controlling what happens when a call *requires approval* but no
# interactive approver is wired into the context. When set, an explicit value
# always wins ("1/true/yes/on" -> fail closed; "0/false/no/off" -> allow + log).
# When unset/empty the default is TTY-aware: fail closed on a non-interactive
# (headless/piped) stdin — where nobody can approve, so silent-allow would be the
# real security hole — and preserve the legacy allow + log when a TTY is attached,
# since no interactive approver is currently wired and hard-denying every
# workspace write would brick the tool in a live session.
_ENFORCE_APPROVAL_ENV = "KODER_ENFORCE_TOOL_APPROVAL"

# Approver signature: (tool_name, arguments, decision) -> awaitable[bool].
# Returns True to allow the call, False to deny it.
Approver = Callable[[str, dict, "PermissionEvaluationResult"], Awaitable[bool]]


@dataclass
class ToolPermissionContext:
    """Active permission context for the current agent run."""

    permission_service: "PermissionService"
    approver: Optional[Approver] = None


_tool_permission_ctx: ContextVar[Optional[ToolPermissionContext]] = ContextVar(
    "tool_permission_context", default=None
)


def set_tool_permission_context(
    permission_service: "PermissionService | None",
    *,
    approver: Optional[Approver] = None,
) -> Token:
    """Publish the active permission context; returns a token for :func:`reset`.

    Passing ``permission_service=None`` clears enforcement (a no-op context), which
    is what subagents / tests without a service want.
    """
    ctx = (
        ToolPermissionContext(permission_service=permission_service, approver=approver)
        if permission_service is not None
        else None
    )
    return _tool_permission_ctx.set(ctx)


def reset_tool_permission_context(token: Token) -> None:
    """Restore the previous permission context."""
    try:
        _tool_permission_ctx.reset(token)
    except (ValueError, LookupError):
        # Token created in a different context (e.g. across task boundaries);
        # best-effort clear instead.
        _tool_permission_ctx.set(None)


def get_tool_permission_context() -> Optional[ToolPermissionContext]:
    return _tool_permission_ctx.get()


def _enforce_approval_when_unattended() -> bool:
    raw = os.environ.get(_ENFORCE_APPROVAL_ENV)
    if raw:
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    # No explicit override: decide by interactivity. A non-interactive stdin
    # means there is genuinely nobody to approve, so we fail closed. Inability
    # to detect a TTY is treated as non-interactive (fail closed) as well.
    try:
        return not sys.stdin.isatty()
    except Exception:
        return True


def _denial_message(tool_name: str, reason: str) -> str:
    base = f"Permission denied for {tool_name}"
    return f"{base}: {reason}" if reason else base


def _dispatch_permission_hooks(event_name: str, tool_name: str, payload: dict) -> object | None:
    """Best-effort hook dispatch for permission events on the main tool chain.

    Returns the hook result (for ``PermissionRequest`` decision inspection) or
    ``None`` when dispatch is unavailable or fails — hooks must never break a
    tool call.
    """
    try:
        from pathlib import Path

        from koder_agent.harness.hooks.runtime import dispatch_command_hooks

        return dispatch_command_hooks(
            cwd=Path.cwd(),
            event_name=event_name,
            match_value=tool_name,
            payload=payload,
        )
    except Exception:
        logger.debug("%s hook dispatch failed for %s", event_name, tool_name, exc_info=True)
        return None


async def enforce_tool_permission(tool_name: str, input_json: str) -> Optional[str]:
    """Return a denial message if the call is blocked, else ``None`` to allow.

    No-op (returns ``None``) when the tool is not guarded, when no permission
    context is active, or when arguments can't be parsed (the tool's own
    validation will then handle the malformed input).

    Dispatches the permission hook events on this path:

    - ``PermissionRequest`` + ``Notification`` when a call needs approval; a
      hook may resolve the request via ``permissionRequestResult`` with
      behavior ``allow`` (optionally ``updatedInput`` is ignored here since
      arguments are already bound) or ``deny``.
    - ``PermissionDenied`` when the final outcome is a denial.
    """
    if tool_name not in GUARDED_TOOLS:
        return None

    ctx = _tool_permission_ctx.get()
    if ctx is None or ctx.permission_service is None:
        return None

    try:
        arguments = json.loads(input_json) if input_json else {}
    except (TypeError, ValueError):
        return None
    if not isinstance(arguments, dict):
        return None

    try:
        decision = await ctx.permission_service.evaluate_tool_call_async(tool_name, arguments)
    except Exception:
        # Never let an evaluation bug crash a tool call; log and fail open so the
        # existing in-tool SecurityGuard still applies as the backstop.
        logger.debug("Permission evaluation failed for %s", tool_name, exc_info=True)
        return None

    def _deny(reason: str) -> str:
        _dispatch_permission_hooks(
            "PermissionDenied",
            tool_name,
            {
                "event": "PermissionDenied",
                "tool_name": tool_name,
                "tool_input": arguments,
                "reason": reason,
            },
        )
        return _denial_message(tool_name, reason)

    if decision.requires_approval:
        request_result = _dispatch_permission_hooks(
            "PermissionRequest",
            tool_name,
            {
                "event": "PermissionRequest",
                "tool_name": tool_name,
                "tool_input": arguments,
                "reason": decision.reason,
            },
        )
        _dispatch_permission_hooks(
            "Notification",
            "permission_prompt",
            {
                "event": "Notification",
                "notification_type": "permission_prompt",
                "tool_name": tool_name,
                "reason": decision.reason,
            },
        )
        hook_decision = getattr(request_result, "permission_request_result", None)
        if isinstance(hook_decision, dict):
            behavior = hook_decision.get("behavior")
            if behavior == "allow":
                return None
            if behavior == "deny":
                return _deny(hook_decision.get("message") or decision.reason)
        if ctx.approver is not None:
            try:
                approved = await ctx.approver(tool_name, arguments, decision)
            except Exception:
                logger.debug("Tool approver raised for %s", tool_name, exc_info=True)
                approved = False
            return None if approved else _deny(decision.reason)
        # No interactive approver available.
        if _enforce_approval_when_unattended():
            return _deny(
                f"{decision.reason}. No approver is available (non-interactive run); "
                "set KODER_ENFORCE_TOOL_APPROVAL=0 to opt out, or use acceptEdits/"
                "bypass mode or an allow rule to permit this call"
            )
        # Interactive fail-open: log at warning so a silent allow is at least
        # visible in a live session.
        logger.warning(
            "Tool %s requires approval but no approver is wired; allowing (%s)",
            tool_name,
            decision.reason,
        )
        return None

    if not decision.allowed:
        return _deny(decision.reason)

    return None
