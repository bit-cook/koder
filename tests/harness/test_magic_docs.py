"""Tests for MagicDocs auto-updating documentation system."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from koder_agent.harness.magic_docs import (
    MAGIC_DOC_HEADER,
    MagicDoc,
    build_magic_doc_refresh_plan,
    clear_tracked_magic_docs,
    create_magic_doc,
    detect_magic_doc_header,
    find_magic_docs,
    format_magic_docs_status,
    get_tracked_magic_docs,
    is_magic_doc,
    refresh_tracked_magic_docs,
    register_magic_doc,
    update_magic_doc,
)


@pytest.fixture(autouse=True)
def clear_magic_doc_tracking():
    clear_tracked_magic_docs()
    yield
    clear_tracked_magic_docs()


def test_magic_doc_header_constant_exists():
    """Test that MAGIC_DOC_HEADER constant is defined."""
    assert MAGIC_DOC_HEADER == "# MAGIC DOC:"


def test_is_magic_doc_detects_header():
    """Test that is_magic_doc returns True for files with magic header."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# MAGIC DOC: Test Document\n\nSome content here.")
        temp_path = Path(f.name)

    try:
        assert is_magic_doc(temp_path) is True
    finally:
        temp_path.unlink()


def test_detect_magic_doc_header_extracts_optional_instructions():
    content = "# MAGIC DOC: Runtime Notes\n\n_Keep this focused on entry points._\n\nBody"

    assert detect_magic_doc_header(content) == (
        "Runtime Notes",
        "Keep this focused on entry points.",
    )


def test_is_magic_doc_returns_false_for_normal_files():
    """Test that is_magic_doc returns False for files without magic header."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Regular Document\n\nThis is not a magic doc.")
        temp_path = Path(f.name)

    try:
        assert is_magic_doc(temp_path) is False
    finally:
        temp_path.unlink()


def test_is_magic_doc_returns_false_for_nonexistent_file():
    """Test that is_magic_doc returns False for files that don't exist."""
    nonexistent_path = Path("/tmp/nonexistent_magic_doc_test_file.md")
    assert is_magic_doc(nonexistent_path) is False


def test_find_magic_docs_finds_magic_docs_in_directory():
    """Test that find_magic_docs finds all magic docs in a directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir)

        # Create magic docs
        magic1 = temp_path / "magic1.md"
        magic1.write_text("# MAGIC DOC: First Doc\n\nContent 1")

        magic2 = temp_path / "magic2.md"
        magic2.write_text("# MAGIC DOC: Second Doc\n\nContent 2")

        # Create a subdirectory with a magic doc
        subdir = temp_path / "subdir"
        subdir.mkdir()
        magic3 = subdir / "magic3.md"
        magic3.write_text("# MAGIC DOC: Third Doc\n\nContent 3")

        magic_docs = find_magic_docs(temp_path)

        assert len(magic_docs) == 3
        assert all(isinstance(doc, MagicDoc) for doc in magic_docs)

        titles = {doc.title for doc in magic_docs}
        assert titles == {"First Doc", "Second Doc", "Third Doc"}


def test_find_magic_docs_ignores_non_magic_files():
    """Test that find_magic_docs ignores files without magic header."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir)

        # Create magic doc
        magic = temp_path / "magic.md"
        magic.write_text("# MAGIC DOC: Magic Doc\n\nMagic content")

        # Create regular markdown
        regular = temp_path / "regular.md"
        regular.write_text("# Regular Doc\n\nRegular content")

        # Create non-markdown file
        other = temp_path / "file.txt"
        other.write_text("# MAGIC DOC: Not a markdown file")

        magic_docs = find_magic_docs(temp_path)

        assert len(magic_docs) == 1
        assert magic_docs[0].title == "Magic Doc"
        assert magic_docs[0].path == magic


def test_create_magic_doc_creates_file_with_header():
    """Test that create_magic_doc creates a file with the magic header."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "new_magic.md"

        magic_doc = create_magic_doc(temp_path, "Test Magic Doc", "This is the initial content.")

        assert temp_path.exists()
        assert isinstance(magic_doc, MagicDoc)
        assert magic_doc.path == temp_path
        assert magic_doc.title == "Test Magic Doc"
        assert magic_doc.content == "This is the initial content."

        # Verify file content
        content = temp_path.read_text()
        assert content.startswith("# MAGIC DOC: Test Magic Doc\n")
        assert "This is the initial content." in content
        assert is_magic_doc(temp_path)


def test_update_magic_doc_preserves_header_updates_content():
    """Test that update_magic_doc preserves the header but updates content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "magic.md"

        # Create initial magic doc
        create_magic_doc(temp_path, "Original Title", "Original content")

        # Update the doc
        update_magic_doc(temp_path, "Updated content here")

        # Verify the update
        content = temp_path.read_text()
        assert content.startswith("# MAGIC DOC: Original Title\n")
        assert "Updated content here" in content
        assert "Original content" not in content
        assert is_magic_doc(temp_path)


def test_update_magic_doc_raises_if_not_magic_doc():
    """Test that update_magic_doc raises an error for non-magic docs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir) / "regular.md"
        temp_path.write_text("# Regular Doc\n\nContent")

        with pytest.raises(ValueError, match="not a magic doc"):
            update_magic_doc(temp_path, "New content")


def test_magic_doc_dataclass_structure():
    """Test that MagicDoc has the expected fields."""
    magic_doc = MagicDoc(
        path=Path("/tmp/test.md"),
        title="Test Title",
        content="Test content",
        last_updated="2026-04-12",
    )

    assert magic_doc.path == Path("/tmp/test.md")
    assert magic_doc.title == "Test Title"
    assert magic_doc.content == "Test content"
    assert magic_doc.last_updated == "2026-04-12"


def test_register_magic_doc_tracks_runtime_state(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# MAGIC DOC: Runtime\n\nBody", encoding="utf-8")

    doc = register_magic_doc(path)

    assert doc is not None
    tracked = get_tracked_magic_docs()
    assert len(tracked) == 1
    assert tracked[0].path == path.resolve()
    assert tracked[0].title == "Runtime"


def test_build_refresh_plan_merges_tracked_and_discovered_docs(tmp_path):
    tracked_path = tmp_path / "tracked.md"
    discovered_path = tmp_path / "docs" / "discovered.md"
    tracked_path.write_text("# MAGIC DOC: Tracked\n\nBody", encoding="utf-8")
    discovered_path.parent.mkdir()
    discovered_path.write_text("# MAGIC DOC: Discovered\n\nBody", encoding="utf-8")
    register_magic_doc(tracked_path)

    plan = build_magic_doc_refresh_plan(tmp_path)

    assert [item.title for item in plan] == ["Discovered", "Tracked"]
    tracked_item = next(item for item in plan if item.title == "Tracked")
    assert tracked_item.tracked is True
    discovered_item = next(item for item in plan if item.title == "Discovered")
    assert discovered_item.tracked is False


def test_refresh_tracked_magic_docs_updates_managed_section(tmp_path):
    path = tmp_path / "architecture.md"
    path.write_text(
        "# MAGIC DOC: Architecture\n\n_Keep the current runtime entry points fresh._\n\nExisting overview.\n",
        encoding="utf-8",
    )
    register_magic_doc(path)

    results = refresh_tracked_magic_docs(
        "We moved runtime state under ~/.koder and project .koder directories.",
        "Koder now documents the runtime state paths and command surface.",
        cwd=tmp_path,
        now=datetime(2026, 5, 5, 9, 30, 0),
    )

    assert len(results) == 1
    assert results[0].changed is True
    content = path.read_text(encoding="utf-8")
    assert content.startswith("# MAGIC DOC: Architecture")
    assert "_Keep the current runtime entry points fresh._" in content
    assert "## Koder Session Notes" in content
    assert "Last refreshed: 2026-05-05 09:30:00" in content
    assert "~/.koder" in content
    assert "koder-magic-docs:auto-refresh-start" in content


def test_refresh_tracked_magic_docs_replaces_existing_managed_section(tmp_path):
    path = tmp_path / "architecture.md"
    path.write_text(
        "# MAGIC DOC: Architecture\n\nBody\n\n"
        "## Koder Session Notes\n\n"
        "<!-- koder-magic-docs:auto-refresh-start -->\n"
        "Last refreshed: old\n\n"
        "- User: old\n"
        "<!-- koder-magic-docs:auto-refresh-end -->\n",
        encoding="utf-8",
    )
    register_magic_doc(path)

    refresh_tracked_magic_docs(
        "New session signal",
        "New assistant signal",
        cwd=tmp_path,
        now=datetime(2026, 5, 5, 10, 0, 0),
    )

    content = path.read_text(encoding="utf-8")
    assert "old" not in content
    assert content.count("## Koder Session Notes") == 1
    assert "New session signal" in content


def test_refresh_removes_unmarked_docs_from_tracking(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# MAGIC DOC: Runtime\n\nBody", encoding="utf-8")
    register_magic_doc(path)
    path.write_text("# Regular Doc\n\nBody", encoding="utf-8")

    result = refresh_tracked_magic_docs("user text", "assistant text", cwd=tmp_path)

    assert result[0].status == "removed"
    assert get_tracked_magic_docs() == []


def test_format_magic_docs_status_lists_discovered_docs(tmp_path):
    path = tmp_path / "docs" / "notes.md"
    path.parent.mkdir()
    path.write_text("# MAGIC DOC: Runtime\n\nBody", encoding="utf-8")

    status = format_magic_docs_status(tmp_path)

    assert "magic_docs:" in status
    assert "discovered: 1" in status
    assert "docs/notes.md: Runtime" in status


@pytest.mark.asyncio
async def test_read_file_registers_magic_docs(tmp_path, monkeypatch):
    from koder_agent.tools.file import read_file

    monkeypatch.setattr("koder_agent.tools.file.truncate_text_by_tokens", lambda text: text)
    path = tmp_path / "notes.md"
    path.write_text("# MAGIC DOC: Runtime\n\nBody", encoding="utf-8")

    result = await read_file.on_invoke_tool(None, json.dumps({"path": str(path)}))

    assert "MAGIC DOC" in result
    assert get_tracked_magic_docs()[0].path == path.resolve()
