"""prompt_toolkit Completer for @file and @agent mentions.

Supports:
- Agent name autocomplete (prefix match, ``*`` icon)
- MCP resource autocomplete (prefix match, ``◇`` icon)
- File fuzzy search (``+`` icon)
- Path-style completion (``~/``, ``./``, ``/`` — directory listing)
- Directory drill-down (selecting a directory appends ``/`` and re-triggers)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

if TYPE_CHECKING:
    from .file_index import ProjectFileIndex

MAX_COMPLETIONS = 15


def _find_at_trigger(text: str) -> int | None:
    """Return the index of a valid ``@`` trigger in *text*, or *None*.

    A valid trigger is ``@`` preceded by whitespace or at position 0.
    We scan backwards from the end of *text* and stop at the first
    whitespace boundary — so only the *last* ``@`` token is considered.
    """
    i = len(text) - 1
    while i >= 0:
        ch = text[i]
        if ch == "@":
            if i == 0 or text[i - 1] in (" ", "\t", "\n"):
                return i
            return None
        if ch in (" ", "\t", "\n"):
            return None
        i -= 1
    return None


def _is_path_like(query: str) -> bool:
    """Return True if *query* looks like a filesystem path."""
    return query.startswith(("~/", "./", "../", "/")) or query in ("~", ".", "..")


def _path_completions(
    query: str, cwd: Path, *, max_results: int = 15
) -> Iterable[tuple[str, bool]]:
    """Yield ``(relative_path, is_dir)`` for path-style queries.

    Resolves ``~``, ``.``, ``..`` and lists directory contents filtered
    by the filename prefix.
    """
    expanded = os.path.expanduser(query)
    if not os.path.isabs(expanded):
        expanded = str(cwd / expanded)
    expanded_path = Path(expanded)

    if expanded_path.is_dir() and query.endswith("/"):
        directory = expanded_path
        prefix = ""
    elif expanded_path.is_dir() and query in ("~", ".", ".."):
        directory = expanded_path
        prefix = ""
    else:
        directory = expanded_path.parent
        prefix = expanded_path.name

    if not directory.is_dir():
        return

    prefix_lower = prefix.lower()
    count = 0
    try:
        for entry in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if count >= max_results:
                break
            if entry.name.startswith("."):
                continue
            if prefix_lower and not entry.name.lower().startswith(prefix_lower):
                continue
            # Build the display path relative to query root
            if query.endswith("/") or not prefix:
                display = query + entry.name
            else:
                # Replace the partial filename with the full name
                display = query[: query.rfind("/") + 1] + entry.name if "/" in query else entry.name
            yield display, entry.is_dir()
            count += 1
    except OSError:
        return


class AtMentionCompleter(Completer):
    """Completer for ``@file``, ``@agent``, and ``@resource`` mentions."""

    def __init__(
        self,
        file_index: ProjectFileIndex,
        agent_names: list[tuple[str, str]] | None = None,
        mcp_resources: list[tuple[str, str]] | None = None,
        cwd: Path | None = None,
    ):
        self._file_index = file_index
        self._agent_names: list[tuple[str, str]] = agent_names or []
        self._mcp_resources: list[tuple[str, str]] = mcp_resources or []
        self._cwd = cwd or Path.cwd()

    def update_agents(self, agents: list[tuple[str, str]]) -> None:
        """Replace the agent list used for completions."""
        self._agent_names = agents

    def update_mcp_resources(self, resources: list[tuple[str, str]]) -> None:
        """Replace the MCP resource list used for completions."""
        self._mcp_resources = resources

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor

        at_pos = _find_at_trigger(text)
        if at_pos is None:
            return

        raw_query = text[at_pos + 1 :]

        # Handle quoted paths: @"partial...
        query = raw_query.lstrip('"')

        # How far back to replace (from cursor to the @)
        start_position = -(len(text) - at_pos)
        count = 0

        # --- Agent completions (prefix match, shown first) ---
        query_lower = query.lower()
        for agent_name, description in self._agent_names:
            if count >= MAX_COMPLETIONS:
                break
            if query_lower and not agent_name.lower().startswith(query_lower):
                continue
            completion_text = f"@{agent_name} "
            yield Completion(
                text=completion_text,
                start_position=start_position,
                display=f"* {agent_name}",
                display_meta=description[:60] if description else "agent",
            )
            count += 1

        # --- MCP resource completions (prefix match) ---
        for res_uri, res_desc in self._mcp_resources:
            if count >= MAX_COMPLETIONS:
                break
            if query_lower and not res_uri.lower().startswith(query_lower):
                continue
            completion_text = f"@{res_uri} "
            yield Completion(
                text=completion_text,
                start_position=start_position,
                display=f"◇ {res_uri}",
                display_meta=res_desc[:60] if res_desc else "resource",
            )
            count += 1

        # --- File completions ---
        remaining = MAX_COMPLETIONS - count
        if remaining <= 0:
            return

        # Path-style completion (~/., ./, /, ..)
        if _is_path_like(query):
            for display_path, is_dir in _path_completions(query, self._cwd, max_results=remaining):
                if is_dir:
                    # Directory: no trailing space — allows drill-down
                    completion_text = f"@{display_path}/"
                    icon = "▸"
                    meta = "dir"
                else:
                    completion_text = f"@{display_path} "
                    icon = "+"
                    meta = "file"
                yield Completion(
                    text=completion_text,
                    start_position=start_position,
                    display=f"{icon} {display_path}{'/' if is_dir else ''}",
                    display_meta=meta,
                )
                count += 1
            return

        # Fuzzy file search (default)
        results = self._file_index.search(query, max_results=remaining)
        for file_path in results:
            needs_quote = " " in file_path
            if needs_quote:
                completion_text = f'@"{file_path}" '
            else:
                completion_text = f"@{file_path} "
            yield Completion(
                text=completion_text,
                start_position=start_position,
                display=f"+ {file_path}",
                display_meta="file",
            )
            count += 1
