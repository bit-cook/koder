"""Tests for micro-compaction (tool result truncation)."""

from koder_agent.harness.memory.micro_compact import (
    DEFAULT_MAX_RESULT_CHARS,
    TRUNCATION_MARKER,
    MicroCompactConfig,
    micro_compact_messages,
)


def test_config_defaults():
    assert DEFAULT_MAX_RESULT_CHARS == 20_000
    assert isinstance(TRUNCATION_MARKER, str)


def test_small_results_unchanged():
    """Messages with small tool results should not be modified."""
    messages = [
        {"role": "user", "content": "Read the file"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc1", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "content": "small content", "tool_call_id": "tc1"},
    ]
    result = micro_compact_messages(messages)
    assert result == messages  # No changes


def test_large_tool_result_truncated():
    """Large tool results should be truncated."""
    large_content = "x" * 50_000
    messages = [
        {"role": "user", "content": "Read the file"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc1", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "tool", "content": large_content, "tool_call_id": "tc1"},
    ]
    result = micro_compact_messages(messages)
    tool_msg = next(m for m in result if m.get("role") == "tool")
    assert len(tool_msg["content"]) < len(large_content)
    assert "output truncated" in tool_msg["content"]


def test_custom_max_chars():
    """Custom max_chars should be respected."""
    content = "x" * 500
    messages = [
        {"role": "tool", "content": content, "tool_call_id": "tc1"},
    ]
    config = MicroCompactConfig(max_result_chars=100)
    result = micro_compact_messages(messages, config=config)
    tool_msg = result[0]
    assert len(tool_msg["content"]) <= 200  # 100 + marker


def test_non_tool_messages_unchanged():
    """User and assistant messages should not be truncated."""
    large_content = "x" * 50_000
    messages = [
        {"role": "user", "content": large_content},
        {"role": "assistant", "content": large_content},
    ]
    result = micro_compact_messages(messages)
    assert result[0]["content"] == large_content
    assert result[1]["content"] == large_content


def test_multiple_tool_results():
    """Multiple tool results should each be evaluated independently."""
    messages = [
        {"role": "tool", "content": "x" * 50_000, "tool_call_id": "tc1"},
        {"role": "tool", "content": "small", "tool_call_id": "tc2"},
        {"role": "tool", "content": "y" * 50_000, "tool_call_id": "tc3"},
    ]
    result = micro_compact_messages(messages)
    assert "output truncated" in result[0]["content"]
    assert result[1]["content"] == "small"
    assert "output truncated" in result[2]["content"]


def test_preserves_head_and_tail():
    """Truncated content should keep head (beginning) of the content."""
    content = "HEADER_START\n" + "middle\n" * 10000 + "FOOTER_END"
    messages = [
        {"role": "tool", "content": content, "tool_call_id": "tc1"},
    ]
    config = MicroCompactConfig(max_result_chars=500)
    result = micro_compact_messages(messages, config=config)
    truncated = result[0]["content"]
    assert "HEADER_START" in truncated  # Head preserved
    assert "output truncated" in truncated


def test_returns_new_list():
    """Should return a new list, not modify in place."""
    messages = [
        {"role": "tool", "content": "x" * 50_000, "tool_call_id": "tc1"},
    ]
    original_content = messages[0]["content"]
    _ = micro_compact_messages(messages)
    assert messages[0]["content"] == original_content  # Original unchanged


def test_token_savings():
    """Should report token savings."""
    messages = [
        {"role": "tool", "content": "x" * 50_000, "tool_call_id": "tc1"},
        {"role": "tool", "content": "y" * 50_000, "tool_call_id": "tc2"},
    ]
    result, savings = micro_compact_messages(messages, return_savings=True)
    assert savings > 0
