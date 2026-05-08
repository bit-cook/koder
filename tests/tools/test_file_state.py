"""Tests for read-before-write enforcement via file state tracking."""

import os
import time

from koder_agent.tools.file_state import ReadFileState


class TestReadFileState:
    def test_record_read(self, tmp_path):
        tracker = ReadFileState()
        f = tmp_path / "test.txt"
        f.write_text("hello")
        tracker.record_read(str(f))
        assert tracker.has_been_read(str(f))

    def test_unread_file(self, tmp_path):
        tracker = ReadFileState()
        assert not tracker.has_been_read(str(tmp_path / "nope.txt"))

    def test_staleness_detection(self, tmp_path):
        tracker = ReadFileState()
        f = tmp_path / "test.txt"
        f.write_text("version1")
        tracker.record_read(str(f))
        assert not tracker.is_stale(str(f))
        time.sleep(0.05)
        f.write_text("version2")
        assert tracker.is_stale(str(f))

    def test_content_fallback_on_mtime_change(self, tmp_path):
        tracker = ReadFileState()
        f = tmp_path / "test.txt"
        f.write_text("same content")
        tracker.record_read(str(f), content="same content")
        time.sleep(0.05)
        os.utime(str(f), None)
        assert not tracker.is_stale(str(f))

    def test_partial_read_is_flagged(self, tmp_path):
        tracker = ReadFileState()
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        tracker.record_read(str(f), is_partial=True)
        assert tracker.has_been_read(str(f))
        assert tracker.is_partial_view(str(f))

    def test_full_read_clears_partial(self, tmp_path):
        tracker = ReadFileState()
        f = tmp_path / "test.txt"
        f.write_text("hello")
        tracker.record_read(str(f), is_partial=True)
        assert tracker.is_partial_view(str(f))
        tracker.record_read(str(f), is_partial=False)
        assert not tracker.is_partial_view(str(f))

    def test_nonexistent_file_not_stale(self, tmp_path):
        tracker = ReadFileState()
        assert not tracker.is_stale(str(tmp_path / "nope.txt"))

    def test_record_read_normalizes_path(self, tmp_path):
        tracker = ReadFileState()
        f = tmp_path / "test.txt"
        f.write_text("hello")
        tracker.record_read(str(tmp_path / "." / "test.txt"))
        assert tracker.has_been_read(str(f))
