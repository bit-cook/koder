"""Bootstrap helpers for the harness runtime."""

from __future__ import annotations

from .commands.registry import CommandRegistry
from .tools import ToolRegistry


def build_registries(permission_service=None) -> tuple[CommandRegistry, ToolRegistry]:
    tool_registry = ToolRegistry.empty(permission_service=permission_service)
    command_registry = CommandRegistry.with_defaults()
    return command_registry, tool_registry
