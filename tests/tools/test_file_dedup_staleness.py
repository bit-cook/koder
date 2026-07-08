"""Test that file state is invalidated after compaction (H7)."""

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

from koder_agent.tools.file_state import ReadFileState


class TestFileStateInvalidation:
    """Verify invalidate_all clears read-state after compaction."""

    def test_invalidate_all_clears_records(self, tmp_path):
        """After invalidate_all, has_been_read returns False."""
        f = tmp_path / "file.py"
        f.write_text("hello")
        state = ReadFileState()
        state.record_read(str(f), content="hello")
        assert state.has_been_read(str(f))

        state.invalidate_all()
        assert not state.has_been_read(str(f))

    def test_after_invalidation_is_not_stale(self, tmp_path):
        """After invalidation, files are unknown (not stale)."""
        f = tmp_path / "file.py"
        f.write_text("hello")
        state = ReadFileState()
        state.record_read(str(f), content="hello")
        state.invalidate_all()
        # Should not be considered stale -- it is just unknown now
        assert not state.is_stale(str(f))

    def test_invalidate_all_clears_multiple_files(self, tmp_path):
        """All tracked files are cleared, not just one."""
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("aaa")
        f2.write_text("bbb")
        state = ReadFileState()
        state.record_read(str(f1), content="aaa")
        state.record_read(str(f2), content="bbb")
        assert state.has_been_read(str(f1))
        assert state.has_been_read(str(f2))

        state.invalidate_all()
        assert not state.has_been_read(str(f1))
        assert not state.has_been_read(str(f2))

    def test_can_re_record_after_invalidation(self, tmp_path):
        """Re-recording after invalidation works normally."""
        f = tmp_path / "file.py"
        f.write_text("hello")
        state = ReadFileState()
        state.record_read(str(f), content="hello")
        state.invalidate_all()
        # Re-record
        state.record_read(str(f), content="hello")
        assert state.has_been_read(str(f))

    def test_get_full_content_none_after_invalidation(self, tmp_path):
        """get_full_content returns None after invalidation."""
        f = tmp_path / "file.py"
        f.write_text("content")
        state = ReadFileState()
        state.record_read(str(f), content="content")
        assert state.get_full_content(str(f)) == "content"

        state.invalidate_all()
        assert state.get_full_content(str(f)) is None
