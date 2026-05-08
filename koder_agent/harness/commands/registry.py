"""Command registry for the harness runtime."""

from __future__ import annotations

from dataclasses import dataclass, field

from .builtins import (
    CONFIG_COMMAND_SPECS,
    INSPECTION_COMMAND_SPECS,
    PLUGIN_COMMAND_SPECS,
    RUNTIME_COMMAND_SPECS,
    SESSION_COMMAND_SPECS,
    WORKFLOW_REVIEW_COMMAND_SPECS,
    WORKFLOW_STATE_COMMAND_SPECS,
)
from .internal import DEBUG_COMMAND_SPECS, DIAGNOSTIC_COMMAND_SPECS


@dataclass(frozen=True)
class CommandSpec:
    """Runtime-facing command descriptor."""

    name: str
    help_text: str
    enabled: bool = True
    aliases: tuple[str, ...] = field(default_factory=tuple)


class CommandRegistry:
    """Registry of known command descriptors."""

    def __init__(
        self, commands: dict[str, CommandSpec] | None = None, aliases: dict[str, str] | None = None
    ):
        self._commands = commands or {}
        self._aliases = aliases or {}

    @classmethod
    def with_defaults(cls) -> "CommandRegistry":
        registry = cls()
        registry._register_builtins(CONFIG_COMMAND_SPECS)
        registry._register_builtins(SESSION_COMMAND_SPECS)
        registry._register_builtins(INSPECTION_COMMAND_SPECS)
        registry._register_builtins(PLUGIN_COMMAND_SPECS)
        registry._register_builtins(RUNTIME_COMMAND_SPECS)
        registry._register_builtins(WORKFLOW_REVIEW_COMMAND_SPECS)
        registry._register_builtins(WORKFLOW_STATE_COMMAND_SPECS)
        return registry

    @classmethod
    def with_all_commands(cls) -> "CommandRegistry":
        registry = cls.with_defaults()
        registry._register_builtins(DEBUG_COMMAND_SPECS)
        registry._register_builtins(DIAGNOSTIC_COMMAND_SPECS)
        return registry

    @classmethod
    def with_all_program_commands(cls) -> "CommandRegistry":
        return cls.with_all_commands()

    def _register_builtins(self, command_specs: dict[str, dict]) -> None:
        for name, metadata in command_specs.items():
            spec = CommandSpec(
                name=name,
                help_text=metadata["help_text"],
                enabled=metadata.get("enabled", True),
                aliases=tuple(metadata.get("aliases", ())),
            )
            self._commands[name] = spec
            for alias in spec.aliases:
                self._aliases[alias] = name

    def list_names(self) -> list[str]:
        return list(self._commands.keys())

    def get(self, name: str) -> CommandSpec | None:
        if name in self._aliases:
            name = self._aliases[name]
        return self._commands.get(name)
