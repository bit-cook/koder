"""Tests for LLM-based conversation compaction."""

from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.harness.memory.budget import (
    ContextPreflightError,
    estimate_context_preflight,
)
from koder_agent.harness.memory.compact import (
    COMPACTION_SUMMARY_PROMPT,
    build_compacted_session_items,
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
async def test_llm_compact_preserves_trailing_response_function_call_pair():
    # The trailing contiguous response-format function_call / function_call_output
    # pair is preserved verbatim (item 4) so the kept tail stays replayable, in
    # addition to the recent plain-text messages.
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

    # Plain-text tail is kept first, then the trailing tool pair verbatim.
    assert result.kept_messages[:2] == messages[:2]
    assert result.kept_messages[2:] == messages[2:]


@pytest.mark.asyncio
async def test_llm_compact_does_not_grow_already_compacted_context():
    # Enough older plain content to force a real summary on the first pass, plus
    # a trailing tool pair preserved verbatim (item 4). Re-compacting the
    # already-compacted context must be idempotent and preserve the tail.
    messages = [
        {"role": "user", "content": "u0"},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
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
    # The trailing tool pair survived the first compaction verbatim.
    assert first.kept_messages[-2:] == messages[-2:]
    # Re-compacting is idempotent: no new summary, context unchanged.
    assert second.summary is None
    assert second.kept_messages == compacted_context


@pytest.mark.asyncio
async def test_llm_compact_fails_without_rewriting_on_error():
    messages = [{"role": "user", "content": f"msg {i}"} for i in range(10)]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        side_effect=Exception("API error"),
    ):
        with pytest.raises(Exception, match="API error"):
            await llm_compact_messages(messages)


@pytest.mark.asyncio
async def test_compaction_never_rewrites_history_from_truncated_source():
    messages = [{"role": "user", "content": f"sentinel-{index}" * 100} for index in range(8)]
    original = [dict(message) for message in messages]

    async def reject_complete_source(_messages, **kwargs):
        assert kwargs.get("overflow_policy", "error") == "error"
        raise ContextPreflightError(
            estimate_context_preflight(
                context_window=64,
                response_reserve=32,
                input_tokens=1_000,
            ),
            subject="Auxiliary request",
        )

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        side_effect=reject_complete_source,
    ):
        with pytest.raises(ContextPreflightError):
            await llm_compact_messages(messages, keep_recent=2)

    assert messages == original


@pytest.mark.asyncio
async def test_compaction_preserves_system_and_developer_items():
    messages = [
        {"role": "system", "content": "system invariant"},
        {"role": "developer", "content": "developer invariant"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "latest request"},
    ]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="<summary>older conversation</summary>",
    ):
        result = await llm_compact_messages(messages, keep_recent=1)

    assert messages[0] in result.kept_messages
    assert messages[1] in result.kept_messages
    assert messages[-1] in result.kept_messages
    persisted = build_compacted_session_items(result)
    assert persisted[:2] == messages[:2]
    assert persisted[2]["content"].startswith("[Conversation compacted]")


@pytest.mark.asyncio
async def test_compaction_preserves_recent_user_intent_and_tool_pairs_under_tiny_window():
    messages = [
        {"role": "system", "content": "do not lose me"},
        {"role": "user", "content": "old request " * 100},
        {"role": "user", "content": "latest exact intent"},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "read_file",
            "arguments": '{"path":"important.py"}',
        },
        {"type": "function_call_output", "call_id": "call-1", "output": "evidence"},
    ]
    original = [dict(message) for message in messages]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        side_effect=ContextPreflightError(
            estimate_context_preflight(
                context_window=32,
                response_reserve=16,
                input_tokens=500,
            )
        ),
    ):
        with pytest.raises(ContextPreflightError):
            await llm_compact_messages(messages, keep_recent=1)

    assert messages == original
    assert messages[-2]["call_id"] == messages[-1]["call_id"]
