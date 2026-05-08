"""Task-local context for tools running inside an agent team."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .service import TeamService


@dataclass(frozen=True)
class TeamToolContext:
    """Runtime metadata for a teammate's tool calls."""

    team_id: str
    sender_name: str
    sender_agent_id: str
    team_service: TeamService
    source: str | None = None


_current_team_tool_context: ContextVar[TeamToolContext | None] = ContextVar(
    "koder_team_tool_context",
    default=None,
)


def get_team_tool_context() -> TeamToolContext | None:
    """Return the current team tool context, if this task is team-scoped."""

    return _current_team_tool_context.get()


@contextmanager
def team_tool_context(context: TeamToolContext | None) -> Iterator[None]:
    """Temporarily expose team metadata to tools called during one agent run."""

    token = _current_team_tool_context.set(context)
    try:
        yield
    finally:
        _current_team_tool_context.reset(token)
