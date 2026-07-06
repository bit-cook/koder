"""Tests for same-second / coarse-mtime staleness detection in ReadFileState.

Covers the audit item ``stale-mtime-partial-same-second``: an in-place edit that
lands within the same coarse mtime tick (or otherwise does not advance mtime)
must still be reported as stale via size and/or content-hash comparison.
"""

import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues.
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.tools.file_state import ReadFileState


@pytest.fixture
def tmp_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "sample.txt"
        path.write_text("line1\nline2\nline3\n", encoding="utf-8")
        yield path


def _freeze_mtime(path: Path) -> float:
    """Pin the file's mtime and return it, simulating a coarse/same-second clock."""
    mtime = os.stat(path).st_mtime
    os.utime(path, (mtime, mtime))
    return mtime


def test_same_second_size_change_detected_as_stale(tmp_file):
    """An in-place edit that changes size but not mtime is detected via size."""
    state = ReadFileState()
    state.record_read(str(tmp_file), content=tmp_file.read_text(encoding="utf-8"))
    frozen = _freeze_mtime(tmp_file)

    # Edit the file, then force mtime back to the recorded value (same-second edit).
    tmp_file.write_text("line1\nline2\nline3\nline4-added\n", encoding="utf-8")
    os.utime(tmp_file, (frozen, frozen))

    assert state.is_stale(str(tmp_file)) is True


def test_same_second_same_size_edit_detected_via_hash(tmp_file):
    """A same-size in-place edit with unchanged mtime is caught by content hash."""
    state = ReadFileState()
    original = tmp_file.read_text(encoding="utf-8")
    state.record_read(str(tmp_file), content=original)
    frozen = _freeze_mtime(tmp_file)

    # Replace content with a same-length but different body.
    same_len_edit = original.replace("line2", "lineX")
    assert len(same_len_edit) == len(original)
    tmp_file.write_text(same_len_edit, encoding="utf-8")
    os.utime(tmp_file, (frozen, frozen))

    assert state.is_stale(str(tmp_file)) is True


def test_partial_read_same_second_edit_detected(tmp_file):
    """Partial reads previously skipped the fallback; size/hash now catch edits."""
    state = ReadFileState()
    # Record as a partial read (no full content stored).
    state.record_read(str(tmp_file), content=None, is_partial=True)
    frozen = _freeze_mtime(tmp_file)

    tmp_file.write_text("line1\nline2\nline3\nEXTRA\n", encoding="utf-8")
    os.utime(tmp_file, (frozen, frozen))

    assert state.is_stale(str(tmp_file)) is True


def test_unchanged_file_not_stale(tmp_file):
    """Non-regression: an untouched file (same mtime/size/content) is not stale."""
    state = ReadFileState()
    state.record_read(str(tmp_file), content=tmp_file.read_text(encoding="utf-8"))
    _freeze_mtime(tmp_file)

    assert state.is_stale(str(tmp_file)) is False


def test_advanced_mtime_but_identical_content_not_stale(tmp_file):
    """Non-regression: touching mtime without changing content stays non-stale."""
    state = ReadFileState()
    content = tmp_file.read_text(encoding="utf-8")
    state.record_read(str(tmp_file), content=content)

    # Advance mtime far into the future but keep identical bytes.
    future = os.stat(tmp_file).st_mtime + 100
    os.utime(tmp_file, (future, future))

    assert state.is_stale(str(tmp_file)) is False


def test_deleted_file_is_conservatively_stale(tmp_file):
    """A file that can no longer be stat'd is treated as stale (conservative)."""
    state = ReadFileState()
    state.record_read(str(tmp_file), content=tmp_file.read_text(encoding="utf-8"))
    tmp_file.unlink()

    assert state.is_stale(str(tmp_file)) is True
