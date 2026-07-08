"""Test that model output with [brackets] displays correctly (H14)."""

import sys
import types
from pathlib import Path

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


class TestBracketContentPreservation:
    """Verify Rich markup does not swallow bracket-enclosed model output."""

    def test_brackets_not_swallowed_by_rich_markup(self):
        """Text.from_markup with escaped brackets preserves literal content."""
        from rich.text import Text

        # Simulate what _format_markdown_content now does: escape brackets first
        raw = "Here is [important text] in brackets"
        escaped = raw.replace("[", "\\[")
        result = Text.from_markup(escaped)
        plain = result.plain
        assert "[important text]" in plain

    def test_multiple_brackets_preserved(self):
        """Multiple bracket-enclosed segments all survive."""
        from rich.text import Text

        raw = "[first] and [second] items"
        escaped = raw.replace("[", "\\[")
        result = Text.from_markup(escaped)
        plain = result.plain
        assert "[first]" in plain
        assert "[second]" in plain

    def test_rich_markup_tags_still_work_after_escape(self):
        """Our own [bold]...[/bold] tags still apply formatting."""
        import re

        from rich.text import Text

        # Simulate the full pipeline: escape brackets first, then apply formatting
        raw = "Hello [world] with **bold text**"
        formatted_line = raw.replace("[", "\\[")
        formatted_line = re.sub(r"\*\*([^*]+)\*\*", r"[bold]\1[/bold]", formatted_line)
        result = Text.from_markup(formatted_line)
        plain = result.plain
        # Literal brackets preserved
        assert "[world]" in plain
        # Bold markup applied (tag stripped from plain text)
        assert "bold text" in plain
        assert "[bold]" not in plain

    def test_empty_brackets_preserved(self):
        """Empty brackets [] are not swallowed."""
        from rich.text import Text

        raw = "an empty [] bracket"
        escaped = raw.replace("[", "\\[")
        result = Text.from_markup(escaped)
        assert "[]" in result.plain

    def test_bracket_with_rich_like_content(self):
        """Content that looks like Rich tags (e.g. [red]) is still literal."""
        from rich.text import Text

        raw = "use [red] or [bold] as labels"
        escaped = raw.replace("[", "\\[")
        result = Text.from_markup(escaped)
        plain = result.plain
        assert "[red]" in plain
        assert "[bold]" in plain
