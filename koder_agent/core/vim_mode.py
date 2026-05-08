"""Vim mode management for the interactive prompt.

Uses prompt_toolkit's built-in vi editing mode. When enabled,
the prompt switches to vi-style editing with INSERT/NORMAL modes,
standard motions (h/j/k/l/w/b/e/$/^/0), operators (d/c/y),
and all other vi features provided by prompt_toolkit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from prompt_toolkit.enums import EditingMode


class VimModeManager:
    """Manages vim mode state for the interactive prompt.

    When enabled, tells prompt_toolkit to use EditingMode.VI
    instead of EditingMode.EMACS (default).
    """

    def __init__(self, state_path: Optional[Path] = None):
        self._enabled = False
        self._state_path = state_path

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def toggle(self) -> str:
        """Toggle vim mode and return status message."""
        self._enabled = not self._enabled
        state = "enabled" if self._enabled else "disabled"
        return f"Vim mode {state}"

    def get_editing_mode(self) -> EditingMode:
        """Get the prompt_toolkit editing mode."""
        return EditingMode.VI if self._enabled else EditingMode.EMACS

    def get_status_text(self) -> str:
        """Get status text for the prompt indicator."""
        if not self._enabled:
            return ""
        return "[VIM]"

    def save(self) -> None:
        """Persist vim mode state to disk."""
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps({"vim_enabled": self._enabled}),
            encoding="utf-8",
        )

    def load(self) -> None:
        """Load vim mode state from disk."""
        if self._state_path is None:
            return
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._enabled = data.get("vim_enabled", False)
        except (json.JSONDecodeError, OSError):
            pass
