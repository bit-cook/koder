"""State models for the harness runtime."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Notification:
    message: str


@dataclass(frozen=True)
class HarnessState:
    mode: str = "boot"
    session_id: str | None = None
    notifications: list[Notification] = field(default_factory=list)
    command_registry: tuple[str, ...] = ()
    tool_registry: tuple[str, ...] = ()
