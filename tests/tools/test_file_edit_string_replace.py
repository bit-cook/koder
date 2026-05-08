"""Tests for string-replacement edit mode."""

import json
import sys
import types
from pathlib import Path

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

# Ensure project root is on sys.path when running tests directly
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.tools.file import (  # noqa: E402
    _file_state,
    edit_file,
    edit_file_by_replacement,
    read_file,
)


@pytest.fixture(autouse=True)
def _reset_file_state():
    """Clear the global file state tracker between tests."""
    _file_state.clear()
    yield
    _file_state.clear()


class TestStringReplacementEdit:
    def test_simple_replacement(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    return 'hi'\n")
        _file_state.record_read(str(f), content=f.read_text())
        result = edit_file_by_replacement(str(f), "return 'hi'", "return 'hello world'")
        assert "Successfully" in result
        assert f.read_text() == "def hello():\n    return 'hello world'\n"

    def test_string_not_found(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    pass\n")
        _file_state.record_read(str(f), content=f.read_text())
        result = edit_file_by_replacement(str(f), "nonexistent string", "replacement")
        assert "not found" in result.lower()

    def test_multiple_matches_without_replace_all(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("foo\nbar\nfoo\nbaz\n")
        _file_state.record_read(str(f), content=f.read_text())
        result = edit_file_by_replacement(str(f), "foo", "qux")
        assert "2 matches" in result.lower() or "replace_all" in result.lower()
        assert f.read_text() == "foo\nbar\nfoo\nbaz\n"  # unchanged

    def test_replace_all_flag(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("foo\nbar\nfoo\nbaz\n")
        _file_state.record_read(str(f), content=f.read_text())
        result = edit_file_by_replacement(str(f), "foo", "qux", replace_all=True)
        assert "Successfully" in result
        assert f.read_text() == "qux\nbar\nqux\nbaz\n"

    def test_old_equals_new_rejected(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello\n")
        result = edit_file_by_replacement(str(f), "hello", "hello")
        assert "same" in result.lower()

    def test_empty_old_string_creates_file(self, tmp_path):
        f = tmp_path / "new_file.py"
        result = edit_file_by_replacement(str(f), "", "# new file\n")
        assert "Created" in result
        assert f.read_text() == "# new file\n"

    def test_empty_old_string_on_existing_file_rejected(self, tmp_path):
        f = tmp_path / "existing.py"
        f.write_text("content\n")
        result = edit_file_by_replacement(str(f), "", "overwrite")
        assert "already exists" in result.lower()

    def test_curly_quote_normalization(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("msg = \u201chello world\u201d\n")
        _file_state.record_read(str(f), content=f.read_text())
        result = edit_file_by_replacement(str(f), 'msg = "hello world"', 'msg = "goodbye"')
        assert "Successfully" in result

    def test_deletion_strips_trailing_newline(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("line1\ndelete_me\nline3\n")
        _file_state.record_read(str(f), content=f.read_text())
        result = edit_file_by_replacement(str(f), "delete_me", "")
        assert "Successfully" in result
        assert f.read_text() == "line1\nline3\n"

    def test_file_not_found(self, tmp_path):
        result = edit_file_by_replacement(str(tmp_path / "nope.py"), "old", "new")
        assert "not found" in result.lower()


class TestEditFileToolDualMode:
    """Test that the edit_file tool function accepts both modes."""

    @pytest.mark.asyncio
    async def test_string_replacement_mode(self, tmp_path):
        """edit_file delegates to string replacement when old_string+new_string provided."""
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    return 'hi'\n")
        _file_state.record_read(str(f), content=f.read_text())

        result = await edit_file.on_invoke_tool(
            None,
            json.dumps(
                {
                    "path": str(f),
                    "old_string": "return 'hi'",
                    "new_string": "return 'hello world'",
                }
            ),
        )
        assert "Successfully" in result
        assert f.read_text() == "def hello():\n    return 'hello world'\n"

    @pytest.mark.asyncio
    async def test_diff_mode_still_works(self, tmp_path):
        """edit_file still works with diff parameter (backward compat)."""
        f = tmp_path / "test.py"
        f.write_text("line1\nold_line\nline3\n")
        # Read first (required by read-before-write)
        await read_file.on_invoke_tool(None, json.dumps({"path": str(f)}))

        diff = "@@ -1,3 +1,3 @@\n line1\n-old_line\n+new_line\n line3\n"
        result = await edit_file.on_invoke_tool(
            None,
            json.dumps({"path": str(f), "diff": diff}),
        )
        assert "Successfully" in result or "applied" in result.lower()

    @pytest.mark.asyncio
    async def test_no_args_returns_error(self, tmp_path):
        """edit_file returns error when neither mode args provided."""
        result = await edit_file.on_invoke_tool(
            None,
            json.dumps({"path": "/tmp/test"}),
        )
        assert "Either" in result or "must be provided" in result.lower()

    @pytest.mark.asyncio
    async def test_replace_all_via_tool(self, tmp_path):
        """edit_file passes replace_all through to string replacement."""
        f = tmp_path / "test.py"
        f.write_text("foo\nbar\nfoo\nbaz\n")
        _file_state.record_read(str(f), content=f.read_text())

        result = await edit_file.on_invoke_tool(
            None,
            json.dumps(
                {
                    "path": str(f),
                    "old_string": "foo",
                    "new_string": "qux",
                    "replace_all": True,
                }
            ),
        )
        assert "Successfully" in result
        assert f.read_text() == "qux\nbar\nqux\nbaz\n"
