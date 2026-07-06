"""Skill context manager for tracking active skill restrictions.

This module provides async-safe state management for skill-based tool restrictions
using Python's contextvars. When a skill with `allowed_tools` is loaded, only
those tools (plus always-allowed tools) can be used.

The restriction model uses UNION semantics:
- Multiple skills with `allowed_tools` accumulate their allowed tools
- Loading a skill without `allowed_tools` is a NO-OP for restrictions; it does
  NOT clear restrictions contributed by previously-loaded restricted skills.
  (Allowing an unrestricted skill to clear restrictions would let the model
  self-escape its sandbox by loading any benign skill.) Use the explicit
  `clear_restrictions()` API to reset state.

Pattern syntax for allowed_tools:
- "read_file"           - Exact tool name match
- "run_shell:git *"     - Shell commands matching glob pattern
- "run_powershell:Get-*" - PowerShell commands matching glob pattern
- "run_shell:*"         - All shell commands allowed
- "*"                   - Wildcard, all tools allowed

Note on empty `allowed_tools`:
- A skill with `allowed_tools: []` (empty list) is treated as "no restrictions"
- This is intentional: empty means "didn't specify restrictions", not "block all"
- To block all tools, you would need explicit tooling support (not yet implemented)
"""

from __future__ import annotations

import fnmatch
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Optional

if TYPE_CHECKING:
    from .skill import Skill

# Context variable to track active skill restrictions (async-safe)
_active_restrictions: ContextVar[Optional["SkillRestrictions"]] = ContextVar(
    "active_skill_restrictions", default=None
)

# Substrings that indicate command/process substitution. A pattern like
# ``git *`` cannot reason about what runs inside ``$(...)`` / backticks, so any
# command containing these is rejected outright instead of glob-matched.
_SUBSTITUTION_MARKERS = ("$(", "`", "<(", ">(", "${")


def _contains_substitution(command: str) -> bool:
    return any(marker in command for marker in _SUBSTITUTION_MARKERS)


def _command_matches_pattern(command: str, pattern: str) -> bool:
    """Return True only if EVERY chained segment of *command* matches *pattern*.

    A naive ``fnmatch(command, "git *")`` lets ``git status; rm -rf /`` through
    because the whole string still starts with ``git ``. We instead split the
    command into segments on shell operators (``;`` ``&&`` ``||`` ``|`` and
    newlines) using the quote-aware tokenizer, then require the pattern to match
    every segment. Command/process substitution is rejected outright because a
    first-token pattern cannot police what runs inside it.

    ``pattern == "*"`` keeps its "allow anything" meaning (the caller uses it as
    an explicit escape hatch), but still rejects substitution smuggling.
    """
    if _contains_substitution(command):
        return False

    segments = _split_command_segments(command)
    if not segments:
        # No runnable segment (e.g. empty or only operators): match only if the
        # pattern would also match the empty/stripped command string.
        return fnmatch.fnmatch(command.strip(), pattern)

    return all(fnmatch.fnmatch(segment, pattern) for segment in segments)


_SEGMENT_SEPARATORS = {"|", "||", "&&", ";", ";;", "&"}
_OPERATOR_ONLY_CHARS = set(";&|<>")


def _split_command_segments(command: str) -> list[str]:
    """Split a command line into per-segment strings on shell operators.

    Uses a quote-aware ``shlex`` tokenizer (``punctuation_chars=True``) so that
    operators inside quotes -- e.g. the ``|`` in ``grep 'a|b'`` -- are NOT
    treated as segment separators. Segments are reconstructed as space-joined
    tokens for glob matching. Falls back to a conservative regex split (stricter,
    never looser) if the command cannot be tokenized (e.g. unbalanced quotes).

    Newlines separate whole commands at execution time (``shell.py`` passes the
    raw string to ``/bin/sh -c``), but ``shlex`` treats ``\\n`` as ordinary
    whitespace and would merge ``git log\\nrm -rf /`` into a single segment that
    fnmatches ``git *``. So the raw command is split on line boundaries FIRST and
    each physical line is tokenized independently.
    """
    import shlex

    segments: list[str] = []
    for line in command.splitlines():
        if not line.strip():
            continue
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            import re

            parts = re.split(r"(?:\|\||&&|[|;&])", line)
            segments.extend(part.strip() for part in parts if part.strip())
            continue

        current: list[str] = []
        for token in tokens:
            if token in _SEGMENT_SEPARATORS or (
                token and all(ch in _OPERATOR_ONLY_CHARS for ch in token)
            ):
                if current:
                    segments.append(" ".join(current))
                    current = []
                continue
            current.append(token)
        if current:
            segments.append(" ".join(current))
    return segments


@dataclass
class SkillRestrictions:
    """Tracks tool restrictions from active skills.

    Uses union semantics: tools from multiple loaded skills are combined.

    Pattern syntax for allowed_tools:
    - "read_file"           - Exact tool name match
    - "run_shell:git *"     - Shell commands matching glob pattern
    - "run_powershell:Get-*" - PowerShell commands matching glob pattern
    - "run_shell:*"         - All shell commands allowed
    - "*"                   - Wildcard, all tools allowed
    """

    # Names of skills that contributed to the current restrictions
    loaded_skills: list[str] = field(default_factory=list)

    # Union of all allowed tools from loaded skills (may include patterns)
    allowed_tools: set[str] = field(default_factory=set)

    # Tools that should always be allowed regardless of skill restrictions
    # - get_skill: Must be able to load different skills to change/escape restrictions
    # - todo_read, todo_write: Task management shouldn't be blocked
    ALWAYS_ALLOWED: ClassVar[frozenset[str]] = frozenset({"get_skill", "todo_read", "todo_write"})

    def is_tool_allowed(self, tool_name: str, tool_args: Optional[str] = None) -> bool:
        """Check if a tool is allowed under current restrictions.

        Supports pattern matching:
        - Exact match: "read_file" matches tool_name="read_file"
        - Wildcard: "*" matches any tool
        - Command pattern: "run_shell:git *" matches run_shell with command starting with "git "
        - Command pattern: "run_powershell:Get-*" matches run_powershell commands

        Args:
            tool_name: The name of the tool to check
            tool_args: JSON string of tool arguments (for command pattern matching)

        Returns:
            True if the tool is allowed, False otherwise
        """
        # Always-allowed tools bypass restrictions
        if tool_name in SkillRestrictions.ALWAYS_ALLOWED:
            return True

        # If no restrictions defined, allow all
        if not self.allowed_tools:
            return True

        # Check each allowed pattern
        for pattern in self.allowed_tools:
            if self._matches_pattern(pattern, tool_name, tool_args):
                return True

        return False

    def _matches_pattern(
        self, pattern: str, tool_name: str, tool_args: Optional[str] = None
    ) -> bool:
        """Check if a tool call matches an allowed pattern.

        Args:
            pattern: The allowed pattern (e.g., "read_file", "run_shell:git *", "*")
            tool_name: The actual tool name being called
            tool_args: JSON string of tool arguments

        Returns:
            True if the pattern matches the tool call
        """
        # Universal wildcard - matches everything
        if pattern == "*":
            return True

        # Check for command pattern syntax: "tool_name:command_pattern"
        if ":" in pattern:
            pattern_tool, command_pattern = pattern.split(":", 1)

            # Tool name must match exactly
            if pattern_tool != tool_name:
                return False

            # For shell tools, match against the command argument
            if tool_name in {"run_shell", "run_powershell"} and tool_args:
                return self._matches_shell_command(command_pattern, tool_args)

            # For git_command, match against the args argument
            if tool_name == "git_command" and tool_args:
                return self._matches_git_command(command_pattern, tool_args)

            # Pattern with ":" but no matching logic - treat as no match
            return False

        # Exact tool name match (or glob pattern on tool name)
        return fnmatch.fnmatch(tool_name, pattern)

    def _matches_shell_command(self, pattern: str, tool_args: str) -> bool:
        """Match a shell command against a glob pattern.

        Args:
            pattern: Glob pattern to match (e.g., "git *", "cat *", "*")
            tool_args: JSON string containing {"command": "..."}

        Returns:
            True if the command matches the pattern
        """
        try:
            args = json.loads(tool_args)
            if not isinstance(args, dict):
                return False
            command = args.get("command", "")
            if not isinstance(command, str):
                return False
            return _command_matches_pattern(command, pattern)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return False

    def _matches_git_command(self, pattern: str, tool_args: str) -> bool:
        """Match a git command against a glob pattern.

        Args:
            pattern: Glob pattern to match (e.g., "status", "commit *", "*")
            tool_args: JSON string containing {"args": "..."}

        Returns:
            True if the git args match the pattern
        """
        try:
            args = json.loads(tool_args)
            if not isinstance(args, dict):
                return False
            git_args = args.get("args", "")
            if not isinstance(git_args, str):
                return False
            # ``git_command`` runs a single ``git <args>`` invocation, but the
            # args string can still smuggle chained commands (``status; rm -rf /``)
            # if consumed by a shell. Reject any segment that does not match the
            # pattern, and reject operators/substitutions the pattern didn't
            # account for -- same defense as the shell matcher below.
            return _command_matches_pattern(git_args, pattern)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return False

    def add_skill(self, skill_name: str, tools: list[str]) -> None:
        """Add a skill's allowed tools to the union.

        Args:
            skill_name: Name of the skill being added
            tools: List of tools the skill allows
        """
        if skill_name not in self.loaded_skills:
            self.loaded_skills.append(skill_name)
        self.allowed_tools.update(tools)


def get_active_restrictions() -> Optional[SkillRestrictions]:
    """Get the currently active skill restrictions.

    Returns:
        SkillRestrictions instance if restrictions are active, None otherwise
    """
    return _active_restrictions.get()


def clear_restrictions() -> None:
    """Clear any active skill restrictions.

    This is an explicit reset API. It is intentionally NOT called when a skill
    without `allowed_tools` is loaded (see module docstring) -- loading an
    unrestricted skill must not erase another skill's active restrictions.
    """
    _active_restrictions.set(None)


def add_skill_restrictions(skill: "Skill") -> None:
    """Add tool restrictions from a loaded skill.

    Uses union semantics: if restrictions already exist, the skill's
    allowed tools are added to the existing set.

    Args:
        skill: The skill whose restrictions should be added
    """
    if not skill.allowed_tools:
        return

    current = _active_restrictions.get()

    if current is None:
        # First skill with restrictions
        current = SkillRestrictions()
        _active_restrictions.set(current)

    current.add_skill(skill.name, skill.allowed_tools)


def has_active_restrictions() -> bool:
    """Check if any skill restrictions are currently active.

    Returns:
        True if restrictions are active, False otherwise
    """
    restrictions = _active_restrictions.get()
    return restrictions is not None and bool(restrictions.allowed_tools)
