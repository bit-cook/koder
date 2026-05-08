"""MCP output limits and timeout configuration."""

import os

_WARNING_THRESHOLD = 10_000  # tokens
_DEFAULT_MAX_TOKENS = 25_000
_DEFAULT_TIMEOUT_SECONDS = 300


def get_timeout_seconds() -> int:
    """Get MCP server timeout from MCP_TIMEOUT env var (ms) or default 300s."""
    raw = os.environ.get("MCP_TIMEOUT")
    if raw:
        try:
            ms = int(raw)
            return max(1, ms // 1000)
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_SECONDS


def get_max_mcp_output_tokens() -> int:
    """Get max MCP output tokens from MAX_MCP_OUTPUT_TOKENS env var or default 25000."""
    raw = os.environ.get("MAX_MCP_OUTPUT_TOKENS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return _DEFAULT_MAX_TOKENS


def check_mcp_output_size(output: str, server_name: str = "") -> str | None:
    """Return a warning message if output exceeds threshold, None otherwise."""
    # Rough token estimate: ~4 chars per token
    estimated_tokens = len(output) // 4
    if estimated_tokens > _WARNING_THRESHOLD:
        max_tokens = get_max_mcp_output_tokens()
        return (
            f"MCP tool output from '{server_name}' is approximately {estimated_tokens} tokens "
            f"(warning threshold: {_WARNING_THRESHOLD}, max: {max_tokens}). "
            f"Consider increasing MAX_MCP_OUTPUT_TOKENS or configuring the server to paginate responses."
        )
    return None
