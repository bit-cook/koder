"""Tests for post-compact context repair."""

import json

import pytest

from koder_agent.harness.memory.post_compact import (
    MAX_FILE_RESTORE_COUNT,
    MAX_FILE_RESTORE_TOKENS,
    PostCompactRepair,
)


def test_constants():
    assert MAX_FILE_RESTORE_COUNT == 5
    assert MAX_FILE_RESTORE_TOKENS == 50_000


def test_collect_recently_accessed_files():
    """Should extract file paths from read_file tool calls in messages."""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "/tmp/a.py"}),
                    }
                },
            ],
        },
        {"role": "tool", "content": "file a content", "tool_call_id": "tc1"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "/tmp/b.py"}),
                    }
                },
            ],
        },
        {"role": "tool", "content": "file b content", "tool_call_id": "tc2"},
    ]
    repair = PostCompactRepair()
    files = repair.collect_recently_accessed_files(messages)
    assert "/tmp/a.py" in files
    assert "/tmp/b.py" in files


def test_collect_most_recent_first():
    """Should return most recently accessed files first."""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "/tmp/first.py"}),
                    }
                },
            ],
        },
        {"role": "tool", "content": "first", "tool_call_id": "tc1"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "/tmp/second.py"}),
                    }
                },
            ],
        },
        {"role": "tool", "content": "second", "tool_call_id": "tc2"},
    ]
    repair = PostCompactRepair()
    files = repair.collect_recently_accessed_files(messages)
    assert files[0] == "/tmp/second.py"  # Most recent first
    assert files[1] == "/tmp/first.py"


def test_collect_deduplicates():
    """Same file read twice should only appear once."""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "/tmp/a.py"}),
                    }
                },
            ],
        },
        {"role": "tool", "content": "first read", "tool_call_id": "tc1"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "/tmp/a.py"}),
                    }
                },
            ],
        },
        {"role": "tool", "content": "second read", "tool_call_id": "tc2"},
    ]
    repair = PostCompactRepair()
    files = repair.collect_recently_accessed_files(messages)
    assert files.count("/tmp/a.py") == 1


def test_collect_limits_to_max():
    """Should return at most MAX_FILE_RESTORE_COUNT files."""
    messages = []
    for i in range(10):
        messages.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"file_path": f"/tmp/file{i}.py"}),
                        }
                    }
                ],
            }
        )
        messages.append({"role": "tool", "content": f"content {i}", "tool_call_id": f"tc{i}"})

    repair = PostCompactRepair()
    files = repair.collect_recently_accessed_files(messages)
    assert len(files) <= MAX_FILE_RESTORE_COUNT


def test_collect_ignores_non_read_tools():
    """Should only extract from read_file tool calls."""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"file_path": "/tmp/written.py", "content": "x"}),
                    }
                },
            ],
        },
        {"role": "tool", "content": "ok", "tool_call_id": "tc1"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"file_path": "/tmp/read.py"}),
                    }
                },
            ],
        },
        {"role": "tool", "content": "content", "tool_call_id": "tc2"},
    ]
    repair = PostCompactRepair()
    files = repair.collect_recently_accessed_files(messages)
    assert "/tmp/written.py" not in files
    assert "/tmp/read.py" in files


@pytest.mark.anyio
async def test_build_attachments_reads_existing(tmp_path):
    """Should re-read files that still exist."""
    test_file = tmp_path / "test.py"
    test_file.write_text("def hello(): pass\n")

    repair = PostCompactRepair()
    attachments = await repair.build_file_restoration_attachments(
        [str(test_file)],
        token_budget=50_000,
    )
    assert len(attachments) == 1
    assert "def hello" in attachments[0]["content"]


@pytest.mark.anyio
async def test_build_attachments_skips_missing():
    """Should skip files that no longer exist."""
    repair = PostCompactRepair()
    attachments = await repair.build_file_restoration_attachments(
        ["/nonexistent/file_xyz.py"],
        token_budget=50_000,
    )
    assert len(attachments) == 0


@pytest.mark.anyio
async def test_build_attachments_respects_budget(tmp_path):
    """Should stop when token budget is exceeded."""
    for i in range(10):
        (tmp_path / f"file{i}.py").write_text("x" * 50000)  # Large files

    repair = PostCompactRepair()
    attachments = await repair.build_file_restoration_attachments(
        [str(tmp_path / f"file{i}.py") for i in range(10)],
        token_budget=5000,  # Very small budget
    )
    assert len(attachments) < 10


@pytest.mark.anyio
async def test_build_attachments_limits_file_count(tmp_path):
    """Should respect MAX_FILE_RESTORE_COUNT."""
    for i in range(10):
        (tmp_path / f"file{i}.py").write_text(f"content {i}")

    repair = PostCompactRepair()
    attachments = await repair.build_file_restoration_attachments(
        [str(tmp_path / f"file{i}.py") for i in range(10)],
        token_budget=999_999,
    )
    assert len(attachments) <= MAX_FILE_RESTORE_COUNT
