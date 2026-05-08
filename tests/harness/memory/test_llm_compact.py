"""Tests for LLM-based conversation compaction."""

from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.harness.memory.compact import (
    COMPACTION_SUMMARY_PROMPT,
    compact_messages,
    compactable_session_items,
    llm_compact_messages,
)


def test_compaction_prompt_has_9_sections():
    sections = [
        "Primary Request",
        "Key Technical Concepts",
        "Files and Code Sections",
        "Errors and Fixes",
        "Problem Solving",
        "All User Messages",
        "Pending Tasks",
        "Current Work",
        "Optional Next Step",
    ]
    for section in sections:
        assert section in COMPACTION_SUMMARY_PROMPT, f"Missing section: {section}"


def test_compaction_prompt_has_analysis_instruction():
    assert "analysis" in COMPACTION_SUMMARY_PROMPT.lower()
    assert "summary" in COMPACTION_SUMMARY_PROMPT.lower()


def test_deterministic_compact_still_works():
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
    result = compact_messages(messages, max_messages=5)
    assert len(result.kept_messages) == 5
    assert result.original_count == 20
    assert result.summary is not None


def test_compactable_session_items_rejects_unknown_empty_messages():
    assistant_tool_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "tc1", "function": {"name": "read_file", "arguments": "{}"}}],
    }
    tool_result = {"role": "tool", "content": "done", "tool_call_id": "tc1"}
    items = [
        {"role": "user", "content": "hello"},
        {"role": "unknown", "content": ""},
        {"type": "function_call", "call_id": "call-1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-1", "output": "done"},
        assistant_tool_call,
        tool_result,
    ]

    compactable = compactable_session_items(items)

    assert compactable == [items[0], items[2], items[3], assistant_tool_call, tool_result]


@pytest.mark.asyncio
async def test_llm_compact_calls_model():
    messages = [
        {"role": "user", "content": "Fix the login bug"},
        {"role": "assistant", "content": "I'll look at auth.py..."},
        {"role": "user", "content": "Good, also check the tests"},
        {"role": "assistant", "content": "Found the issue in line 42..."},
    ]
    mock_summary = "Summary: Fixed login bug in auth.py line 42."

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_summary,
    ):
        result = await llm_compact_messages(messages)
        assert result.summary is not None
        assert "Summary" in result.summary or "login" in result.summary.lower()


@pytest.mark.asyncio
async def test_llm_compact_keeps_recent_user_messages():
    messages = [{"role": "user", "content": f"User message {i}"} for i in range(10)]
    # Interleave with assistant messages
    full_messages = []
    for i, msg in enumerate(messages):
        full_messages.append(msg)
        full_messages.append({"role": "assistant", "content": f"Response {i}"})

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="Summary of conversation.",
    ):
        result = await llm_compact_messages(full_messages, keep_recent=4)
        # Should keep at least the last few messages
        assert len(result.kept_messages) >= 4


@pytest.mark.asyncio
async def test_llm_compact_strips_images():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What's in this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        },
        {"role": "assistant", "content": "I see a diagram."},
        {"role": "user", "content": "Thanks!"},
    ]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="Summary: discussed an image.",
    ):
        result = await llm_compact_messages(messages)
        assert result.summary is not None


@pytest.mark.asyncio
async def test_llm_compact_summarizes_tool_pairs_instead_of_replaying_them():
    messages = [
        {"role": "user", "content": "Read the file"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "tc1", "function": {"name": "read_file", "arguments": '{"path": "t.py"}'}}
            ],
        },
        {"role": "tool", "content": "file contents", "tool_call_id": "tc1"},
        {"role": "assistant", "content": "The file contains..."},
        {"role": "user", "content": "Now edit it"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "tc2", "function": {"name": "edit_file", "arguments": '{"path": "t.py"}'}}
            ],
        },
        {"role": "tool", "content": "edited", "tool_call_id": "tc2"},
        {"role": "assistant", "content": "Done editing."},
    ]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="Summary: read and edited t.py.",
    ):
        result = await llm_compact_messages(messages, keep_recent=4)

    assert all("tool_calls" not in msg for msg in result.kept_messages)
    assert all(msg.get("role") != "tool" for msg in result.kept_messages)
    assert [msg["content"] for msg in result.kept_messages] == [
        "Read the file",
        "The file contains...",
        "Now edit it",
        "Done editing.",
    ]


@pytest.mark.asyncio
async def test_llm_compact_summarizes_response_function_call_pairs():
    messages = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"type": "function_call", "call_id": "call-1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-1", "output": "file contents"},
    ]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="Summary: read a file.",
    ):
        result = await llm_compact_messages(messages, keep_recent=2)

    assert result.kept_messages == messages[:2]
    assert all("type" not in msg for msg in result.kept_messages)


@pytest.mark.asyncio
async def test_llm_compact_does_not_grow_already_compacted_context():
    messages = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"type": "function_call", "call_id": "call-1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-1", "output": "file contents"},
    ]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="Summary: read a file.",
    ):
        first = await llm_compact_messages(messages, keep_recent=2)
        compacted_context = [
            {"role": "user", "content": f"[Conversation compacted]\n\n{first.summary}"},
            *first.kept_messages,
        ]
        second = await llm_compact_messages(compacted_context, keep_recent=2)

    assert first.summary is not None
    assert second.summary is None
    assert second.kept_messages == compacted_context


@pytest.mark.asyncio
async def test_llm_compact_fallback_on_error():
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(10)]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        side_effect=Exception("API error"),
    ):
        result = await llm_compact_messages(messages)
        # Should fall back to deterministic compaction
        assert result is not None
        assert result.kept_messages is not None
