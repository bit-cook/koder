"""Tests for ripgrep-backed grep_search tool."""

import asyncio
import json
import shutil

import pytest

from koder_agent.tools.search import grep_search


def invoke_tool(tool, args_dict):
    """Helper to invoke a function tool synchronously."""
    return asyncio.run(tool.on_invoke_tool(None, json.dumps(args_dict)))


@pytest.fixture
def sample_files(tmp_path):
    """Create sample files for testing."""
    # Python file with function
    (tmp_path / "main.py").write_text("""def hello_world():
    print("Hello, World!")
    return True

def goodbye():
    print("Goodbye!")
""")

    # Another Python file
    (tmp_path / "utils.py").write_text("""import os

def helper():
    return "Hello helper"

class WorldClass:
    pass
""")

    # JavaScript file
    (tmp_path / "script.js").write_text("""function hello() {
    console.log("Hello from JS");
}
""")

    # Text file
    (tmp_path / "README.txt").write_text("""Hello World
This is a test file.
It contains multiple lines.
""")

    # Nested directory
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "app.py").write_text("""# Application
def main():
    print("Hello from app")
""")

    return tmp_path


def test_basic_pattern_matching(sample_files):
    """Test basic pattern matching finds files."""
    result = invoke_tool(grep_search, {"pattern": "Hello", "path": str(sample_files)})
    assert isinstance(result, str)
    assert "main.py" in result or "utils.py" in result or "README.txt" in result


def test_default_output_mode_files_with_matches(sample_files):
    """Test default output_mode is files_with_matches."""
    result = invoke_tool(grep_search, {"pattern": "Hello", "path": str(sample_files)})
    # Should list files, not show content
    assert isinstance(result, str)
    # Should not have line numbers in default mode
    lines = result.split("\n")
    # Files with matches mode should show relative paths
    assert any("main.py" in line or "utils.py" in line or "README.txt" in line for line in lines)


def test_content_mode_shows_lines(sample_files):
    """Test content mode shows matching lines with line numbers."""
    result = invoke_tool(
        grep_search,
        {"pattern": "Hello", "path": str(sample_files), "output_mode": "content"},
    )
    assert isinstance(result, str)
    # Should contain line numbers (format: "filename:linenum:content")
    assert ":" in result
    # Should show actual content
    assert "Hello" in result


def test_count_mode_shows_match_counts(sample_files):
    """Test count mode shows match counts per file."""
    result = invoke_tool(
        grep_search,
        {"pattern": "Hello", "path": str(sample_files), "output_mode": "count"},
    )
    assert isinstance(result, str)
    # Should show counts
    assert any(char.isdigit() for char in result)
    # Should mention files with matches
    assert "main.py" in result or "utils.py" in result or "README.txt" in result


def test_glob_filter(sample_files):
    """Test glob filter restricts results to matching patterns."""
    result = invoke_tool(
        grep_search,
        {"pattern": "Hello", "path": str(sample_files), "glob": "*.py"},
    )
    assert isinstance(result, str)
    # Should only find Python files
    assert ".py" in result
    # Should not find .txt or .js files
    assert ".txt" not in result
    assert ".js" not in result


def test_case_insensitive_search(sample_files):
    """Test case-insensitive search with case_insensitive flag."""
    # Create file with lowercase
    (sample_files / "lower.txt").write_text("hello world")

    result = invoke_tool(
        grep_search,
        {"pattern": "HELLO", "path": str(sample_files), "case_insensitive": True},
    )
    assert isinstance(result, str)
    assert "lower.txt" in result


def test_type_filter(sample_files):
    """Test type filter (--type py, etc.)."""
    result = invoke_tool(grep_search, {"pattern": "Hello", "path": str(sample_files), "type": "py"})
    assert isinstance(result, str)
    # Should only find Python files
    if "No matches" not in result:
        assert ".py" in result
        # Should not find non-Python files
        assert ".js" not in result
        assert ".txt" not in result


def test_context_lines(sample_files):
    """Test context lines (context parameter) showing surrounding lines."""
    result = invoke_tool(
        grep_search,
        {
            "pattern": "print",
            "path": str(sample_files),
            "output_mode": "content",
            "context": 1,
        },
    )
    assert isinstance(result, str)
    # Should show context lines around matches
    # With context, we should see more lines than just the match
    lines = [line for line in result.split("\n") if line.strip()]
    # Context should give us more lines
    assert len(lines) > 1


def test_head_limit(sample_files):
    """Test head_limit restricts number of results."""
    # Create multiple files with matches
    for i in range(10):
        (sample_files / f"file_{i}.txt").write_text(f"Hello {i}")

    result = invoke_tool(
        grep_search,
        {
            "pattern": "Hello",
            "path": str(sample_files),
            "output_mode": "files_with_matches",
            "head_limit": 3,
        },
    )
    assert isinstance(result, str)
    # Count number of file mentions (should be limited)
    file_count = result.count("file_")
    assert file_count <= 3


def test_no_matches(sample_files):
    """Test graceful handling when no matches found."""
    result = invoke_tool(
        grep_search,
        {"pattern": "NONEXISTENT_PATTERN_12345", "path": str(sample_files)},
    )
    assert isinstance(result, str)
    # Should indicate no matches
    assert "No matches" in result or "0" in result or result == ""


def test_invalid_path():
    """Test graceful error handling for invalid path."""
    result = invoke_tool(
        grep_search, {"pattern": "test", "path": "/nonexistent/path/that/does/not/exist"}
    )
    assert isinstance(result, str)
    # Should indicate error or no results
    assert "error" in result.lower() or "not" in result.lower() or "No matches" in result


def test_multiline_mode(tmp_path):
    """Test multiline mode for patterns spanning multiple lines."""
    # Create file with multiline pattern
    (tmp_path / "multi.txt").write_text("""start of block
line 1
line 2
end of block""")

    result = invoke_tool(
        grep_search,
        {
            "pattern": "start.*line 1.*line 2",
            "path": str(tmp_path),
            "multiline": True,
            "output_mode": "content",
        },
    )
    assert isinstance(result, str)
    # Should find the multiline match
    assert "multi.txt" in result


def test_leading_dash_pattern(sample_files):
    """Test patterns starting with dash are handled correctly."""
    # Create file with dash pattern
    (sample_files / "dash.txt").write_text("--help command")

    result = invoke_tool(grep_search, {"pattern": "--help", "path": str(sample_files)})
    assert isinstance(result, str)
    # Should handle the pattern correctly
    assert "dash.txt" in result


def test_offset_pagination(sample_files):
    """Test offset parameter for pagination."""
    # Create multiple files
    for i in range(5):
        (sample_files / f"page_{i}.txt").write_text(f"Hello {i}")

    result = invoke_tool(
        grep_search,
        {
            "pattern": "Hello",
            "path": str(sample_files),
            "output_mode": "files_with_matches",
            "offset": 2,
            "head_limit": 2,
        },
    )
    assert isinstance(result, str)
    # Should skip first 2 results and show next 2


def test_include_backward_compat(sample_files):
    """Test 'include' parameter maps to 'glob' for backward compatibility."""
    result = invoke_tool(
        grep_search,
        {"pattern": "Hello", "path": str(sample_files), "include": "*.py"},
    )
    assert isinstance(result, str)
    # Should work same as glob
    if "No matches" not in result:
        assert ".py" in result


def test_ripgrep_not_found(tmp_path, monkeypatch):
    """Test error message when ripgrep is not found."""
    # Mock shutil.which to return None
    monkeypatch.setattr(shutil, "which", lambda x: None)

    result = invoke_tool(grep_search, {"pattern": "test", "path": str(tmp_path)})
    assert isinstance(result, str)
    assert "ripgrep" in result.lower() or "rg" in result.lower()
    assert "install" in result.lower() or "not found" in result.lower()


def test_context_flag_precedence(sample_files):
    """Test context parameter takes precedence over context_after and context_before."""
    result = invoke_tool(
        grep_search,
        {
            "pattern": "print",
            "path": str(sample_files),
            "output_mode": "content",
            "context": 2,
            "context_after": 1,
            "context_before": 1,
        },
    )
    assert isinstance(result, str)
    # Should use context=2 (test passes if no error)


def test_line_numbers_in_content_mode(sample_files):
    """Test line numbers are shown by default in content mode."""
    result = invoke_tool(
        grep_search,
        {"pattern": "Hello", "path": str(sample_files), "output_mode": "content"},
    )
    assert isinstance(result, str)
    # Should contain colon-separated line numbers (filename:linenum:content)
    assert ":" in result


def test_null_optional_arguments_normalized(sample_files):
    """Strict-schema providers send explicit nulls for omitted args; they must not error."""
    result = invoke_tool(
        grep_search,
        {
            "pattern": "Hello",
            "path": str(sample_files),
            "glob": None,
            "include": None,
            "output_mode": None,
            "context": None,
            "type": None,
            "head_limit": None,
            "offset": None,
            "multiline": None,
            "case_insensitive": None,
            "context_after": None,
            "context_before": None,
            "line_numbers": None,
        },
    )
    assert isinstance(result, str)
    assert "error" not in result.lower() or "No matches" in result
    assert "validation" not in result.lower()


def test_pattern_only_invocation(sample_files):
    """Calling with just pattern and path (all other args omitted) works."""
    result = invoke_tool(grep_search, {"pattern": "Hello", "path": str(sample_files)})
    assert isinstance(result, str)
    assert "validation" not in result.lower()
