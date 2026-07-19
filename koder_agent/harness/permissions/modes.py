"""Permission mode definitions for the harness runtime."""

from __future__ import annotations

from enum import Enum


class PermissionMode(str, Enum):
    """Runtime permission modes."""

    DEFAULT = "default"
    STRICT = "strict"
    BYPASS = "bypass"
    PLAN = "plan"
    ACCEPT_EDITS = "acceptEdits"
    DONT_ASK = "dontAsk"


READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "list_directory",
        "glob_search",
        "grep_search",
        "code_intelligence",
        "todo_read",
        "web_search",
        "web_fetch",
        "get_skill",
    }
)

FILE_WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "append_file",
        "notebook_edit",
    }
)
