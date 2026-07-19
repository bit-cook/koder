"""Task-local display routing for tool and subagent activity."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import Context, ContextVar, copy_context
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ToolDisplayCall:
    """Identity of the model tool call currently executing in this task."""

    tool_name: str
    call_id: str | None


@dataclass(frozen=True)
class SubagentDisplayIdentity:
    """Stable identity used to group one child run in the parent display."""

    group_id: str
    agent_id: str
    label: str
    parent_call_id: str | None = None
    order: int | None = None


SubagentDisplayEventKind = Literal[
    "started",
    "tool_started",
    "tool_finished",
    "completed",
    "failed",
    "cancelled",
]


@dataclass(frozen=True)
class SubagentDisplayEvent:
    """Structured child activity consumed by the parent terminal renderer."""

    identity: SubagentDisplayIdentity
    kind: SubagentDisplayEventKind
    tool_name: str | None = None
    detail: str | None = None


SubagentDisplaySink = Callable[[SubagentDisplayEvent], None]

_CURRENT_TOOL_CALL: ContextVar[ToolDisplayCall | None] = ContextVar(
    "koder_current_display_tool_call",
    default=None,
)
_SUBAGENT_DISPLAY_SINK: ContextVar[SubagentDisplaySink | None] = ContextVar(
    "koder_subagent_display_sink",
    default=None,
)

logger = logging.getLogger(__name__)


def current_tool_display_call() -> ToolDisplayCall | None:
    """Return the parent model tool call active in this task, if any."""

    return _CURRENT_TOOL_CALL.get()


def has_subagent_display_sink() -> bool:
    """Return whether the current task is attached to a parent renderer."""

    return _SUBAGENT_DISPLAY_SINK.get() is not None


@contextmanager
def tool_display_call_scope(tool_name: str, call_id: str | None) -> Iterator[None]:
    """Publish one model tool invocation to nested in-process work."""

    token = _CURRENT_TOOL_CALL.set(ToolDisplayCall(tool_name=tool_name, call_id=call_id))
    try:
        yield
    finally:
        _CURRENT_TOOL_CALL.reset(token)


@contextmanager
def subagent_display_scope(sink: SubagentDisplaySink) -> Iterator[None]:
    """Route child activity to the renderer that owns the current terminal."""

    token = _SUBAGENT_DISPLAY_SINK.set(sink)
    try:
        yield
    finally:
        _SUBAGENT_DISPLAY_SINK.reset(token)


def detached_display_context() -> Context:
    """Copy the current task context without parent-owned display routing.

    ``asyncio.create_task`` copies context variables by default. Long-lived
    background agents must not retain a renderer or tool-call identity from the
    interactive turn that launched them.
    """

    context = copy_context()
    context.run(_CURRENT_TOOL_CALL.set, None)
    context.run(_SUBAGENT_DISPLAY_SINK.set, None)
    return context


def emit_subagent_display_event(event: SubagentDisplayEvent) -> bool:
    """Send an event to the active parent renderer without writing stdout."""

    sink = _SUBAGENT_DISPLAY_SINK.get()
    if sink is None:
        return False
    try:
        sink(event)
    except Exception:
        # Rendering is observational. A broken or already-closed display must
        # never change the result of an otherwise successful child run.
        logger.debug("Subagent display sink failed", exc_info=True)
        return False
    return True
