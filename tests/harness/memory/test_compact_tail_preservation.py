"""Tests for item 4: llm_compact_messages preserves the trailing tool pair.

The compacted tail must stay replayable: the last contiguous run of replayable
function_call / function_call_output items is kept verbatim (appended after the
summary + plain-text tail), with tool_call/tool_result pairing intact and no
orphan outputs.
"""

from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.harness.memory.compact import (
    _trailing_replayable_tool_items,
    is_replayable_session_item,
    llm_compact_messages,
)

# ---------------------------------------------------------------------------
# Pure helper: _trailing_replayable_tool_items
# ---------------------------------------------------------------------------


def test_trailing_helper_extracts_last_tool_pair():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "answer"},
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "contents"},
    ]
    tail = _trailing_replayable_tool_items(messages)
    assert tail == messages[2:]


def test_trailing_helper_stops_at_non_tool_item():
    messages = [
        {"type": "function_call", "call_id": "c0", "name": "grep", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c0", "output": "match"},
        {"role": "assistant", "content": "reasoned about it"},  # breaks the run
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "contents"},
    ]
    tail = _trailing_replayable_tool_items(messages)
    # Only the trailing contiguous pair, not the earlier one before the text.
    assert tail == messages[3:]


def test_trailing_helper_drops_orphan_leading_output():
    # A trailing output whose matching call is BEFORE a text break is an orphan
    # and must be dropped so we never emit an unpaired output.
    messages = [
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{}"},
        {"role": "assistant", "content": "thinking"},  # breaks contiguity
        {"type": "function_call_output", "call_id": "c1", "output": "contents"},
    ]
    tail = _trailing_replayable_tool_items(messages)
    assert tail == []  # the lone trailing output has no matching call in the tail


def test_trailing_helper_keeps_paired_call_and_output_together():
    messages = [
        {"role": "user", "content": "hi"},
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "contents"},
    ]
    tail = _trailing_replayable_tool_items(messages)
    call_ids_calls = {m["call_id"] for m in tail if m["type"] == "function_call"}
    call_ids_outputs = {m["call_id"] for m in tail if m["type"] == "function_call_output"}
    assert call_ids_outputs.issubset(call_ids_calls)


def test_trailing_helper_no_tools():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "bye"},
    ]
    assert _trailing_replayable_tool_items(messages) == []


# ---------------------------------------------------------------------------
# llm_compact_messages preserves the tail (target behavior for item 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_keeps_last_tool_pair_replayable():
    messages = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "file contents"},
    ]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="<summary>read a file</summary>",
    ):
        result = await llm_compact_messages(messages, keep_recent=2)

    # The trailing tool pair is preserved verbatim (not summarized away).
    call = next(m for m in result.kept_messages if m.get("type") == "function_call")
    output = next(m for m in result.kept_messages if m.get("type") == "function_call_output")
    assert call["call_id"] == output["call_id"] == "c1"
    assert output["output"] == "file contents"

    # And it is genuinely replayable by the SDK.
    assert is_replayable_session_item(call)
    assert is_replayable_session_item(output)


@pytest.mark.asyncio
async def test_compaction_tail_paired_and_ordered_after_plain_tail():
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"type": "function_call", "call_id": "c9", "name": "edit_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c9", "output": "edited"},
    ]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="<summary>edited a file</summary>",
    ):
        result = await llm_compact_messages(messages, keep_recent=2)

    kept = result.kept_messages
    # Plain-text tail (kept_recent=2 latest plain items) comes first, then the
    # trailing tool pair.
    assert kept[-2] == {
        "type": "function_call",
        "call_id": "c9",
        "name": "edit_file",
        "arguments": "{}",
    }
    assert kept[-1] == {"type": "function_call_output", "call_id": "c9", "output": "edited"}
    # No orphan output: every output has its call present in the kept tail.
    kept_call_ids = {m["call_id"] for m in kept if m.get("type") == "function_call"}
    kept_out_ids = {m["call_id"] for m in kept if m.get("type") == "function_call_output"}
    assert kept_out_ids.issubset(kept_call_ids)


@pytest.mark.asyncio
async def test_compaction_no_trailing_tools_unchanged_behavior():
    """Non-regression: with no trailing tool items, only plain text is kept."""
    messages = [{"role": "user", "content": f"m{i}"} for i in range(8)]
    messages += [{"role": "assistant", "content": "final answer"}]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="<summary>a conversation</summary>",
    ):
        result = await llm_compact_messages(messages, keep_recent=2)

    assert result.summary is not None
    assert all(m.keys() == {"role", "content"} for m in result.kept_messages)
    assert all("type" not in m for m in result.kept_messages)


@pytest.mark.asyncio
async def test_compaction_tail_not_dropped_never_loses_items():
    """The trailing tool pair must survive even when it would otherwise be
    entirely summarized (all items older than keep_recent)."""
    messages = [
        {"role": "user", "content": "u"},
        {"type": "function_call", "call_id": "cX", "name": "run_shell", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "cX", "output": "ok"},
    ]

    with patch(
        "koder_agent.harness.memory.compact.llm_completion",
        new_callable=AsyncMock,
        return_value="<summary>ran a shell command</summary>",
    ):
        result = await llm_compact_messages(messages, keep_recent=1)

    # The pair is preserved despite keep_recent=1 only capturing the single
    # plain-text user message.
    assert {m.get("type") for m in result.kept_messages} >= {
        "function_call",
        "function_call_output",
    }
