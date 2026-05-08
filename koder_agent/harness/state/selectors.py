"""Selectors for the harness runtime state."""

from __future__ import annotations

from .models import HarnessState


def get_mode(state: HarnessState) -> str:
    return state.mode


def get_notifications(state: HarnessState):
    return state.notifications


def get_session_id(state: HarnessState):
    return state.session_id


def get_registry_views(state: HarnessState) -> dict[str, tuple[str, ...]]:
    return {
        "commands": state.command_registry,
        "tools": state.tool_registry,
    }
