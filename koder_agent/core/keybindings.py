"""Configurable keybinding system.

Loads default keybindings, merges with user overrides from
~/.koder/keybindings.json, provides lookup API.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional


class KeyAction(str, Enum):
    """Named actions that can be bound to keys."""

    SUBMIT = "submit"
    CANCEL = "cancel"
    NEWLINE = "newline"
    EXIT = "exit"
    SEARCH = "search"
    SEARCH_NEXT = "search_next"
    SEARCH_PREV = "search_prev"
    COMPLETE = "complete"
    COMPLETE_PREV = "complete_prev"
    ACCEPT_SUGGESTION = "accept_suggestion"
    VOICE = "voice"
    VIM_TOGGLE = "vim_toggle"


# Default keybindings (prompt_toolkit key notation)
DEFAULT_KEYBINDINGS: dict[str, str] = {
    "submit": "enter",
    "cancel": "c-c",
    "newline": "c-j",
    "newline_alt": "escape enter",
    "exit": "c-d",
    "search": "c-r",
    "search_next": "down",
    "search_prev": "up",
    "complete": "tab",
    "complete_prev": "s-tab",
    "accept_suggestion": "right",
    "voice": "space",
    "delete_char": "backspace",
    "delete_forward": "delete",
}


def normalize_key_sequence(key: str) -> str:
    """Return normalized prompt_toolkit key notation or raise ValueError."""
    normalized = " ".join(key.split())
    if not normalized:
        raise ValueError("key cannot be empty")

    from prompt_toolkit.key_binding import KeyBindings

    try:
        KeyBindings().add(*normalized.split())(lambda _event: None)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return normalized


class KeybindingManager:
    """Manages keybindings with user overrides.

    Usage:
        mgr = KeybindingManager()  # Uses ~/.koder/keybindings.json
        key = mgr.get_key("submit")  # Returns "enter" or user override
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path
        self._overrides: dict[str, str | None] = {}
        self._load()

    def _load(self) -> None:
        """Load user overrides from config file."""
        if self._config_path is None:
            return
        if not self._config_path.exists():
            return
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._overrides = data
        except (json.JSONDecodeError, OSError):
            pass

    def get_key(self, action: str) -> str | None:
        """Get the key binding for an action.

        Returns None if the action is unbound (null override) or unknown.
        """
        if action in self._overrides:
            return self._overrides[action]  # May be None (unbind)
        return DEFAULT_KEYBINDINGS.get(action)

    def get_all_bindings(self) -> dict[str, str | None]:
        """Get all effective bindings (defaults + overrides)."""
        result = dict(DEFAULT_KEYBINDINGS)
        result.update(self._overrides)
        return result

    def set_override(self, action: str, key: str | None) -> None:
        """Set a user override for an action."""
        if key is not None:
            key = normalize_key_sequence(key)
        self._overrides[action] = key

    def reset(self, action: str) -> None:
        """Remove user override, reverting to default."""
        self._overrides.pop(action, None)

    def save(self) -> None:
        """Save overrides to config file."""
        if self._config_path is None:
            return
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(self._overrides, indent=2),
            encoding="utf-8",
        )
