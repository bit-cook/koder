"""Tests for micro-compaction (tool result truncation)."""

from koder_agent.harness.memory.micro_compact import (
    DEFAULT_MAX_RESULT_CHARS,
    ENABLED_ENV,
    MAX_CHARS_ENV,
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


# --- function_call_output (openai-agents / Responses) shape ---------------


def test_function_call_output_truncated():
    """Large function_call_output items should have their output truncated."""
    large_output = "z" * 100_000
    messages = [
        {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": large_output},
    ]
    result = micro_compact_messages(messages)

    # Item count and ordering preserved.
    assert len(result) == 2
    assert result[0] == messages[0]

    out_item = result[1]
    assert out_item["type"] == "function_call_output"
    assert out_item["call_id"] == "c1"  # call_id preserved
    assert len(out_item["output"]) < len(large_output)
    assert "output truncated" in out_item["output"]


def test_function_call_output_small_unchanged():
    """Small function_call_output items should be untouched."""
    messages = [
        {"type": "function_call_output", "call_id": "c1", "output": "tiny output"},
    ]
    result = micro_compact_messages(messages)
    assert result == messages


def test_function_call_output_non_string_output_unchanged():
    """Non-string outputs (e.g. structured lists) are passed through untouched."""
    structured = [{"type": "output_text", "text": "x" * 50_000}]
    messages = [
        {"type": "function_call_output", "call_id": "c1", "output": structured},
    ]
    result = micro_compact_messages(messages)
    assert result[0]["output"] is structured


def test_call_ids_preserved_mixed_shapes():
    """Both tool-role and function_call_output call ids survive truncation."""
    messages = [
        {"role": "tool", "content": "a" * 50_000, "tool_call_id": "tc1"},
        {"type": "function_call_output", "call_id": "c2", "output": "b" * 50_000},
    ]
    result = micro_compact_messages(messages)
    assert result[0]["tool_call_id"] == "tc1"
    assert result[1]["call_id"] == "c2"
    assert "output truncated" in result[0]["content"]
    assert "output truncated" in result[1]["output"]


# --- config toggle / env vars --------------------------------------------


def test_disabled_config_is_noop():
    """When disabled, oversized outputs must be passed through untouched."""
    messages = [
        {"role": "tool", "content": "x" * 50_000, "tool_call_id": "tc1"},
        {"type": "function_call_output", "call_id": "c1", "output": "y" * 50_000},
    ]
    config = MicroCompactConfig(enabled=False)
    result, savings = micro_compact_messages(messages, config=config, return_savings=True)
    assert savings == 0
    assert result[0]["content"] == "x" * 50_000
    assert result[1]["output"] == "y" * 50_000


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv(MAX_CHARS_ENV, raising=False)
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    config = MicroCompactConfig.from_env()
    assert config.enabled is True
    assert config.max_result_chars == DEFAULT_MAX_RESULT_CHARS


def test_from_env_custom_max_chars(monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "1234")
    config = MicroCompactConfig.from_env()
    assert config.max_result_chars == 1234


def test_from_env_invalid_max_chars_falls_back(monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "not-a-number")
    config = MicroCompactConfig.from_env()
    assert config.max_result_chars == DEFAULT_MAX_RESULT_CHARS


def test_from_env_zero_max_chars_falls_back(monkeypatch):
    monkeypatch.setenv(MAX_CHARS_ENV, "0")
    config = MicroCompactConfig.from_env()
    assert config.max_result_chars == DEFAULT_MAX_RESULT_CHARS


def test_from_env_toggle_off(monkeypatch):
    for value in ("0", "false", "no", "off", "OFF", "False"):
        monkeypatch.setenv(ENABLED_ENV, value)
        config = MicroCompactConfig.from_env()
        assert config.enabled is False, value


def test_from_env_toggle_on_by_default(monkeypatch):
    monkeypatch.setenv(ENABLED_ENV, "1")
    assert MicroCompactConfig.from_env().enabled is True
    monkeypatch.setenv(ENABLED_ENV, "true")
    assert MicroCompactConfig.from_env().enabled is True
