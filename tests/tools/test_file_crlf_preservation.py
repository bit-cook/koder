"""Tests that file tools preserve the on-disk line-ending style (CRLF/CR/LF).

Editing one line of a CRLF file must not silently rewrite every line ending
to LF -- that corrupts CRLF-sensitive files (.ps1, .bat, .csproj, files with
gitattributes eol=crlf) and turns a one-line edit into a whole-file diff.
"""

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
    append_file,
    edit_file,
    edit_file_by_replacement,
    write_file,
)


@pytest.fixture(autouse=True)
def _reset_file_state():
    """Clear the global file state tracker between tests."""
    _file_state.clear()
    yield
    _file_state.clear()


def _make_file(tmp_path, name: str, data: bytes) -> Path:
    f = tmp_path / name
    f.write_bytes(data)
    return f


def _record_read(f: Path) -> None:
    """Mimic read_file's state tracking (universal-newline normalized)."""
    normalized = f.read_text(encoding="utf-8")
    _file_state.record_read(str(f), content=normalized)


# =============================================================================
# edit_file (string replacement) on CRLF / CR files
# =============================================================================


class TestEditFileCRLFPreservation:
    def test_edit_crlf_file_preserves_crlf_everywhere(self, tmp_path):
        f = _make_file(tmp_path, "script.ps1", b"line1\r\nline2\r\nline3\r\n")
        _record_read(f)

        # old_string uses \n because read_file exposes LF-normalized content
        result = edit_file_by_replacement(str(f), "line2", "LINE2")

        assert "Successfully" in result
        assert f.read_bytes() == b"line1\r\nLINE2\r\nline3\r\n"

    def test_edit_crlf_multiline_old_string_with_lf_matches(self, tmp_path):
        f = _make_file(tmp_path, "build.bat", b"a\r\nb\r\nc\r\nd\r\n")
        _record_read(f)

        # Multiline old_string spans a CRLF boundary but contains only \n
        result = edit_file_by_replacement(str(f), "b\nc", "B\nC")

        assert "Successfully" in result
        assert f.read_bytes() == b"a\r\nB\r\nC\r\nd\r\n"

    def test_edit_cr_only_file_preserves_cr(self, tmp_path):
        f = _make_file(tmp_path, "legacy.txt", b"line1\rline2\rline3\r")
        _record_read(f)

        result = edit_file_by_replacement(str(f), "line2", "LINE2")

        assert "Successfully" in result
        assert f.read_bytes() == b"line1\rLINE2\rline3\r"

    def test_edit_mixed_file_converges_to_dominant_crlf(self, tmp_path):
        # 3 CRLF endings vs 1 lone LF: CRLF is dominant and wins
        f = _make_file(tmp_path, "mixed.txt", b"a\r\nb\r\nc\nd\r\n")
        _record_read(f)

        result = edit_file_by_replacement(str(f), "b", "B")

        assert "Successfully" in result
        assert f.read_bytes() == b"a\r\nB\r\nc\r\nd\r\n"

    def test_edit_mixed_tie_breaks_to_lf(self, tmp_path):
        # 1 CRLF vs 1 lone LF: tie is documented to break in favor of LF
        f = _make_file(tmp_path, "tie.txt", b"a\r\nb\nc")
        _record_read(f)

        result = edit_file_by_replacement(str(f), "c", "C")

        assert "Successfully" in result
        assert f.read_bytes() == b"a\nb\nC"

    def test_edit_lf_file_stays_lf(self, tmp_path):
        f = _make_file(tmp_path, "plain.py", b"line1\nline2\nline3\n")
        _record_read(f)

        result = edit_file_by_replacement(str(f), "line2", "LINE2")

        assert "Successfully" in result
        assert f.read_bytes() == b"line1\nLINE2\nline3\n"

    def test_edit_file_without_newlines_unchanged_behavior(self, tmp_path):
        f = _make_file(tmp_path, "one.txt", b"single line no newline")
        _record_read(f)

        result = edit_file_by_replacement(str(f), "single", "only")

        assert "Successfully" in result
        assert f.read_bytes() == b"only line no newline"


# =============================================================================
# write_file on CRLF files
# =============================================================================


class TestWriteFileCRLFPreservation:
    @pytest.mark.asyncio
    async def test_write_over_crlf_file_preserves_crlf(self, tmp_path):
        f = _make_file(tmp_path, "app.csproj", b"old1\r\nold2\r\n")
        _record_read(f)

        result = await write_file.on_invoke_tool(
            None, json.dumps({"path": str(f), "content": "new1\nnew2\n"})
        )

        assert "Updated" in result
        assert f.read_bytes() == b"new1\r\nnew2\r\n"

    @pytest.mark.asyncio
    async def test_write_over_lf_file_stays_lf(self, tmp_path):
        f = _make_file(tmp_path, "plain.txt", b"old\n")
        _record_read(f)

        result = await write_file.on_invoke_tool(
            None, json.dumps({"path": str(f), "content": "new1\nnew2\n"})
        )

        assert "Updated" in result
        assert f.read_bytes() == b"new1\nnew2\n"

    @pytest.mark.asyncio
    async def test_write_new_file_uses_lf(self, tmp_path):
        f = tmp_path / "brand_new.txt"

        result = await write_file.on_invoke_tool(
            None, json.dumps({"path": str(f), "content": "a\nb\n"})
        )

        assert "Created" in result
        assert f.read_bytes() == b"a\nb\n"


# =============================================================================
# append_file on CRLF files
# =============================================================================


class TestAppendFileCRLFPreservation:
    @pytest.mark.asyncio
    async def test_append_to_crlf_file_uses_crlf(self, tmp_path):
        f = _make_file(tmp_path, "log.txt", b"a\r\nb\r\n")
        _record_read(f)

        result = await append_file.on_invoke_tool(
            None, json.dumps({"path": str(f), "content": "c\nd\n"})
        )

        assert "Appended" in result
        data = f.read_bytes()
        assert data == b"a\r\nb\r\nc\r\nd\r\n"
        # No lone \n anywhere: every \n is preceded by \r
        assert data.count(b"\n") == data.count(b"\r\n")

    @pytest.mark.asyncio
    async def test_append_leading_newline_join_is_crlf(self, tmp_path):
        f = _make_file(tmp_path, "log.txt", b"a\r\nb\r\n")
        _record_read(f)

        result = await append_file.on_invoke_tool(
            None, json.dumps({"path": str(f), "content": "\nappended"})
        )

        assert "Appended" in result
        data = f.read_bytes()
        assert data == b"a\r\nb\r\n\r\nappended"
        assert data.count(b"\n") == data.count(b"\r\n")

    @pytest.mark.asyncio
    async def test_append_to_lf_file_stays_lf(self, tmp_path):
        f = _make_file(tmp_path, "log.txt", b"a\nb\n")
        _record_read(f)

        result = await append_file.on_invoke_tool(
            None, json.dumps({"path": str(f), "content": "c\n"})
        )

        assert "Appended" in result
        assert f.read_bytes() == b"a\nb\nc\n"

    @pytest.mark.asyncio
    async def test_append_creates_new_file_with_lf(self, tmp_path):
        f = tmp_path / "new_append.txt"

        result = await append_file.on_invoke_tool(
            None, json.dumps({"path": str(f), "content": "first\nsecond\n"})
        )

        assert "Appended" in result
        assert f.read_bytes() == b"first\nsecond\n"


# =============================================================================
# edit_file (diff mode) on CRLF files
# =============================================================================


class TestDiffModeCRLFPreservation:
    @pytest.mark.asyncio
    async def test_diff_edit_crlf_file_preserves_crlf(self, tmp_path):
        f = _make_file(tmp_path, "conf.txt", b"line1\r\nold_line\r\nline3\r\n")
        _record_read(f)

        diff = "@@ -1,3 +1,3 @@\n line1\n-old_line\n+new_line\n line3\n"
        result = await edit_file.on_invoke_tool(None, json.dumps({"path": str(f), "diff": diff}))

        assert "Successfully" in result
        assert f.read_bytes() == b"line1\r\nnew_line\r\nline3\r\n"

    @pytest.mark.asyncio
    async def test_diff_edit_lf_file_stays_lf(self, tmp_path):
        f = _make_file(tmp_path, "conf.txt", b"line1\nold_line\nline3\n")
        _record_read(f)

        diff = "@@ -1,3 +1,3 @@\n line1\n-old_line\n+new_line\n line3\n"
        result = await edit_file.on_invoke_tool(None, json.dumps({"path": str(f), "diff": diff}))

        assert "Successfully" in result
        assert f.read_bytes() == b"line1\nnew_line\nline3\n"


class TestStalenessConsistencyAfterEdit:
    """F4-1: after an edit/append whose input carried literal CRLF, the recorded
    state must be LF-normalized so is_stale() (which re-reads with universal
    newlines) does not spuriously report the file as modified."""

    @pytest.mark.asyncio
    async def test_edit_with_literal_crlf_in_new_string_not_stale(self, tmp_path):
        from koder_agent.tools.file import edit_file, get_file_state

        f = _make_file(tmp_path, "u.txt", b"line1\nline2\nline3\n")
        _record_read(f)
        r = await edit_file.on_invoke_tool(
            None,
            json.dumps({"path": str(f), "old_string": "line2", "new_string": "X\r\nY"}),
        )
        assert "edited" in r.lower() or "diff" in r.lower()
        # The file must not be considered stale immediately after our own edit.
        assert get_file_state().is_stale(str(f)) is False

    @pytest.mark.asyncio
    async def test_append_not_stale_after_write(self, tmp_path):
        from koder_agent.tools.file import append_file, get_file_state

        f = _make_file(tmp_path, "log.txt", b"a\r\nb\r\n")
        _record_read(f)
        await append_file.on_invoke_tool(None, json.dumps({"path": str(f), "content": "c\nd\n"}))
        assert get_file_state().is_stale(str(f)) is False
