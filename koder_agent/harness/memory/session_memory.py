"""Session-scoped memory snapshot container."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Thresholds for extraction triggers
INIT_TOKEN_THRESHOLD = 10_000
GROWTH_TOKEN_THRESHOLD = 5_000
MIN_TOOL_CALLS_BETWEEN_UPDATES = 3

# 10-section notes template
SESSION_NOTES_TEMPLATE = """# Session Title
[Brief title describing this session's objective]

# Current State
[What's working, what's in progress, what's blocked]

# Task Specification
[Core requirements and acceptance criteria for this session]

# Files and Functions
[Key files, modules, functions, classes being modified or referenced]

# Workflow
[Recurring patterns, commands, or steps used in this session]

# Errors & Corrections
[Bugs encountered, misunderstandings corrected, mistakes fixed]

# Codebase Documentation
[Architectural insights, conventions, patterns discovered about the codebase]

# Learnings
[Insights about tools, techniques, or approaches that worked well or poorly]

# Key Results
[What was accomplished, decisions made, artifacts produced]

# Worklog
[Chronological list of major actions taken during the session]
"""


@dataclass(frozen=True)
class SessionMemory:
    """Stable session memory snapshot."""

    messages: list[dict] = field(default_factory=list)
    summary: str | None = None
    extracted_memories: list[dict] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "SessionMemory":
        return cls()

    def snapshot(self) -> dict:
        return {
            "messages": list(self.messages),
            "summary": self.summary,
            "extracted_memories": list(self.extracted_memories),
        }


class SessionMemoryManager:
    """Manages session memory extraction and structured notes."""

    def __init__(self, project_dir: Path | str | None = None):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self._last_extraction_tokens: int | None = None
        self._last_extraction_tool_calls: int | None = None

    @property
    def notes_path(self) -> Path:
        """Path to session notes file."""
        return self.project_dir / ".koder" / "session-memory" / "notes.md"

    def should_extract(self, token_count: int, tool_call_count: int) -> bool:
        """
        Determine if extraction should be triggered.

        Rules:
        1. Must have at least MIN_TOOL_CALLS_BETWEEN_UPDATES tool calls since last extraction
        2. First extraction: when token_count >= INIT_TOKEN_THRESHOLD
        3. Subsequent extractions: when growth >= GROWTH_TOKEN_THRESHOLD since last extraction
        """
        # Must have minimum tool call activity
        if self._last_extraction_tool_calls is not None:
            if tool_call_count - self._last_extraction_tool_calls < MIN_TOOL_CALLS_BETWEEN_UPDATES:
                return False
        else:
            # First extraction: need at least MIN_TOOL_CALLS_BETWEEN_UPDATES total
            if tool_call_count < MIN_TOOL_CALLS_BETWEEN_UPDATES:
                return False

        # First extraction
        if self._last_extraction_tokens is None:
            return token_count >= INIT_TOKEN_THRESHOLD

        # Subsequent extractions
        growth = token_count - self._last_extraction_tokens
        return growth >= GROWTH_TOKEN_THRESHOLD

    def record_extraction(self, token_count: int, tool_call_count: int) -> None:
        """Record that an extraction occurred at the given counts."""
        self._last_extraction_tokens = token_count
        self._last_extraction_tool_calls = tool_call_count

    def ensure_notes_file(self) -> Path:
        """
        Ensure the notes file exists with template content.
        Creates parent directories if needed.
        Does NOT overwrite existing content.
        """
        path = self.notes_path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(SESSION_NOTES_TEMPLATE)
        return path
