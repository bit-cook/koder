"""Ghost text (auto-suggest) for the interactive prompt.

Provides dim inline suggestions from two sources:
1. **Input history** — prefix-matches previous user inputs.
2. **Mid-input slash commands** — detects ``/partial`` mid-input and suggests
   the rest of the command name (e.g., typing `` /com`` ghosts ``mit``).
"""

from __future__ import annotations

import re
from typing import Dict

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

_MID_SLASH_RE = re.compile(r"\s/([a-zA-Z0-9_:-]*)$")


class KoderAutoSuggest(AutoSuggest):
    """Combined auto-suggest: input history + mid-input slash ghost text."""

    def __init__(
        self,
        commands: Dict[str, str] | None = None,
        max_history: int = 200,
    ):
        self._commands = commands or {}
        self._history: list[str] = []
        self._max_history = max_history
        self._speculative_suggestion: str | None = None

    def update_commands(self, commands: Dict[str, str]) -> None:
        """Replace the slash-command dict used for ghost text."""
        self._commands = commands

    def record_input(self, text: str) -> None:
        """Record a user input for future history suggestions."""
        text = text.strip()
        self.clear_speculative_suggestion()
        if not text:
            return
        # Deduplicate: remove old occurrence, push to front
        try:
            self._history.remove(text)
        except ValueError:
            pass
        self._history.insert(0, text)
        if len(self._history) > self._max_history:
            self._history.pop()

    def reset_history(self) -> None:
        """Clear recorded input history for a fresh interactive session."""
        self._history.clear()
        self.clear_speculative_suggestion()

    def set_speculative_suggestion(self, text: str | None) -> None:
        """Set a post-turn ghost suggestion for an empty prompt."""

        cleaned = " ".join((text or "").strip().split())
        self._speculative_suggestion = cleaned or None

    def clear_speculative_suggestion(self) -> None:
        """Clear the post-turn ghost suggestion."""

        self._speculative_suggestion = None

    def get_speculative_suggestion(self) -> str | None:
        """Return the currently queued post-turn suggestion."""

        return self._speculative_suggestion

    def get_suggestion(self, buffer: Buffer, document: Document) -> Suggestion | None:
        text = document.text_before_cursor

        # Don't suggest if cursor isn't at the end
        if document.cursor_position < len(document.text):
            return None

        if not text:
            if self._speculative_suggestion:
                return Suggestion(self._speculative_suggestion)
            return None

        if self._speculative_suggestion:
            suggestion_lower = self._speculative_suggestion.lower()
            text_lower = text.lower()
            if suggestion_lower.startswith(text_lower) and suggestion_lower != text_lower:
                return Suggestion(self._speculative_suggestion[len(text) :])

        # --- 1. Mid-input slash command ghost text ---
        suggestion = self._slash_ghost(text)
        if suggestion is not None:
            return suggestion

        # --- 2. Input history prefix match ---
        return self._history_ghost(text)

    def _slash_ghost(self, text: str) -> Suggestion | None:
        """Return ghost text for a mid-input ``/command`` partial."""
        # Also handle start-of-input slash (but only if no space yet)
        if text.startswith("/") and " " not in text:
            partial = text[1:]
        else:
            m = _MID_SLASH_RE.search(text)
            if not m:
                return None
            partial = m.group(1)

        if not partial:
            return None

        partial_lower = partial.lower()
        for cmd_name in self._commands:
            if cmd_name.lower().startswith(partial_lower) and cmd_name.lower() != partial_lower:
                suffix = cmd_name[len(partial) :]
                return Suggestion(suffix)
        return None

    def _history_ghost(self, text: str) -> Suggestion | None:
        """Return ghost text from a previous input that prefix-matches."""
        for prev in self._history:
            if prev.startswith(text) and prev != text:
                return Suggestion(prev[len(text) :])
        return None
