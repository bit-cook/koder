"""Tests for MCP output limits and timeout configuration."""

from koder_agent.mcp.limits import (
    check_mcp_output_size,
    get_max_mcp_output_tokens,
    get_timeout_seconds,
)


def test_default_max_tokens():
    assert get_max_mcp_output_tokens() == 25_000


def test_custom_max_tokens(monkeypatch):
    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "50000")
    assert get_max_mcp_output_tokens() == 50_000


def test_invalid_max_tokens_falls_back(monkeypatch):
    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "not_a_number")
    assert get_max_mcp_output_tokens() == 25_000


def test_zero_max_tokens_clamps_to_one(monkeypatch):
    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "0")
    assert get_max_mcp_output_tokens() == 1


def test_default_timeout():
    assert get_timeout_seconds() == 300


def test_custom_timeout_ms(monkeypatch):
    monkeypatch.setenv("MCP_TIMEOUT", "10000")
    assert get_timeout_seconds() == 10


def test_invalid_timeout_falls_back(monkeypatch):
    monkeypatch.setenv("MCP_TIMEOUT", "not_a_number")
    assert get_timeout_seconds() == 300


def test_small_timeout_clamps_to_one(monkeypatch):
    monkeypatch.setenv("MCP_TIMEOUT", "500")
    assert get_timeout_seconds() == 1


def test_output_warning_below_threshold():
    small_output = "x" * 1000
    assert check_mcp_output_size(small_output) is None


def test_output_warning_above_threshold():
    # ~10001 tokens at 4 chars/token = 40004 chars
    large_output = "x" * 40_004
    warning = check_mcp_output_size(large_output, server_name="test-server")
    assert warning is not None
    assert "test-server" in warning
    assert "10000" in warning  # threshold mentioned


def test_output_warning_at_threshold():
    # Exactly at threshold: 10000 tokens * 4 chars = 40000 chars
    output = "x" * 40_000
    assert check_mcp_output_size(output) is None


def test_output_warning_includes_max_tokens(monkeypatch):
    monkeypatch.setenv("MAX_MCP_OUTPUT_TOKENS", "99999")
    large_output = "x" * 50_000
    warning = check_mcp_output_size(large_output)
    assert warning is not None
    assert "99999" in warning
