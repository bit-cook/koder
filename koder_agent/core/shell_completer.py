"""Shell completions for ``!`` prefix commands.

When the user types ``!cmd``, this completer provides shell command and
file-path suggestions via ``compgen``.
"""

from __future__ import annotations

import subprocess
from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

MAX_RESULTS = 15
TIMEOUT_SECONDS = 1


class ShellCompleter(Completer):
    """Provide completions for ``!<command>`` shell shortcut."""

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor

        if not text.startswith("!"):
            return

        # Everything after the !
        shell_input = text[1:]

        # Determine what to complete
        parts = shell_input.split()
        if not parts or (len(parts) == 1 and not shell_input.endswith(" ")):
            # Completing the command name
            prefix = parts[0] if parts else ""
            yield from self._command_completions(prefix)
        else:
            # Completing an argument (file path)
            prefix = parts[-1] if not shell_input.endswith(" ") else ""
            yield from self._file_completions(prefix)

    def _command_completions(self, prefix: str) -> Iterable[Completion]:
        """Yield shell command completions via ``compgen -c``."""
        results = self._run_compgen("c", prefix)
        for cmd in results:
            yield Completion(
                text=cmd,
                start_position=-len(prefix),
                display=cmd,
                display_meta="command",
            )

    def _file_completions(self, prefix: str) -> Iterable[Completion]:
        """Yield file path completions via ``compgen -f``."""
        results = self._run_compgen("f", prefix)
        for path in results:
            yield Completion(
                text=path,
                start_position=-len(prefix),
                display=path,
                display_meta="path",
            )

    @staticmethod
    def _run_compgen(flag: str, prefix: str) -> list[str]:
        """Run ``compgen -<flag> <prefix>`` and return results."""
        try:
            result = subprocess.run(
                ["bash", "-c", f"compgen -{flag} -- {_shell_quote(prefix)}"],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode != 0:
                return []
            lines = [line for line in result.stdout.splitlines() if line]
            return lines[:MAX_RESULTS]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return []


def _shell_quote(s: str) -> str:
    """Minimal shell quoting for compgen prefix."""
    if not s:
        return "''"
    # Single-quote the string, escaping existing single quotes
    return "'" + s.replace("'", "'\\''") + "'"
