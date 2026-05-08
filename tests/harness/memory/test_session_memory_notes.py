"""Tests for session memory notes template and trigger logic."""

from koder_agent.harness.memory.session_memory import (
    GROWTH_TOKEN_THRESHOLD,
    INIT_TOKEN_THRESHOLD,
    MIN_TOOL_CALLS_BETWEEN_UPDATES,
    SESSION_NOTES_TEMPLATE,
    SessionMemory,
    SessionMemoryManager,
)


def test_template_has_10_sections():
    sections = [
        "Session Title",
        "Current State",
        "Task Specification",
        "Files and Functions",
        "Workflow",
        "Errors & Corrections",
        "Codebase Documentation",
        "Learnings",
        "Key Results",
        "Worklog",
    ]
    for section in sections:
        assert section in SESSION_NOTES_TEMPLATE, f"Missing: {section}"


def test_thresholds():
    assert INIT_TOKEN_THRESHOLD == 10_000
    assert GROWTH_TOKEN_THRESHOLD == 5_000
    assert MIN_TOOL_CALLS_BETWEEN_UPDATES == 3


def test_should_extract_below_init():
    mgr = SessionMemoryManager()
    assert not mgr.should_extract(token_count=5_000, tool_call_count=5)


def test_should_extract_above_init():
    mgr = SessionMemoryManager()
    assert mgr.should_extract(token_count=12_000, tool_call_count=5)


def test_should_extract_subsequent_not_enough_growth():
    mgr = SessionMemoryManager()
    mgr.record_extraction(token_count=12_000, tool_call_count=5)
    assert not mgr.should_extract(token_count=14_000, tool_call_count=7)


def test_should_extract_subsequent_enough_growth():
    mgr = SessionMemoryManager()
    mgr.record_extraction(token_count=12_000, tool_call_count=5)
    assert mgr.should_extract(token_count=18_000, tool_call_count=10)


def test_should_not_extract_without_tool_calls():
    mgr = SessionMemoryManager()
    assert not mgr.should_extract(token_count=50_000, tool_call_count=1)


def test_notes_path(tmp_path):
    mgr = SessionMemoryManager(project_dir=tmp_path)
    expected = tmp_path / ".koder" / "session-memory" / "notes.md"
    assert mgr.notes_path == expected


def test_ensure_notes_file(tmp_path):
    mgr = SessionMemoryManager(project_dir=tmp_path)
    path = mgr.ensure_notes_file()
    assert path.exists()
    content = path.read_text()
    assert "Session Title" in content


def test_ensure_notes_file_idempotent(tmp_path):
    mgr = SessionMemoryManager(project_dir=tmp_path)
    path1 = mgr.ensure_notes_file()
    path1.write_text("custom content")
    path2 = mgr.ensure_notes_file()
    assert path2.read_text() == "custom content"  # Should NOT overwrite


def test_session_memory_backward_compat():
    """Existing SessionMemory dataclass should still work."""
    mem = SessionMemory.empty()
    assert mem.messages == []
    assert mem.summary is None
    snap = mem.snapshot()
    assert "messages" in snap
