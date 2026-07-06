"""MCP output limits and timeout configuration."""

from __future__ import annotations

import os
from typing import Any

_WARNING_THRESHOLD = 10_000  # tokens
_DEFAULT_MAX_TOKENS = 25_000
_DEFAULT_TIMEOUT_SECONDS = 300

# Rough token estimate used throughout this module: ~4 chars per token.
_CHARS_PER_TOKEN = 4


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
    estimated_tokens = len(output) // _CHARS_PER_TOKEN
    if estimated_tokens > _WARNING_THRESHOLD:
        max_tokens = get_max_mcp_output_tokens()
        return (
            f"MCP tool output from '{server_name}' is approximately {estimated_tokens} tokens "
            f"(warning threshold: {_WARNING_THRESHOLD}, max: {max_tokens}). "
            f"Consider increasing MAX_MCP_OUTPUT_TOKENS or configuring the server to paginate responses."
        )
    return None


def truncate_mcp_text(
    text: str,
    server_name: str = "",
    max_tokens: int | None = None,
) -> str:
    """Truncate an MCP text payload to the configured token budget.

    The budget is expressed in tokens; we convert to a character budget using
    the same ~4-chars-per-token estimate the rest of this module relies on. If
    the text is within budget it is returned unchanged. Otherwise it is cut at
    the character budget and a clear marker is appended so the model knows the
    payload was clipped.
    """
    if max_tokens is None:
        max_tokens = get_max_mcp_output_tokens()

    estimated_tokens = len(text) // _CHARS_PER_TOKEN
    if estimated_tokens <= max_tokens:
        return text

    char_budget = max_tokens * _CHARS_PER_TOKEN
    label = f" from '{server_name}'" if server_name else ""
    marker = f"\n\n[MCP output{label} truncated: {estimated_tokens} tokens -> limit {max_tokens}]"
    return text[:char_budget] + marker


def truncate_call_tool_result(result: Any, server_name: str = "") -> Any:
    """Truncate oversized text content on an MCP ``CallToolResult`` in place.

    MCP tools are executed by the SDK's ``MCPServer`` rather than koder's own
    ``function_tool`` wrapper, so the SDK-level output cap never runs on them.
    This helper enforces :func:`get_max_mcp_output_tokens` by clipping the
    combined text content of a tool result. It mutates and returns *result*.

    The truncation is applied to the aggregate size of all text blocks: once the
    running total exceeds the budget, remaining text blocks are trimmed (and a
    marker appended to the block that crosses the limit). Non-text content
    (images, embedded resources) is left untouched. Any object lacking a
    ``content`` list is returned unchanged so this is safe to call defensively.
    """
    content = getattr(result, "content", None)
    if not isinstance(content, list):
        return result

    max_tokens = get_max_mcp_output_tokens()
    char_budget = max_tokens * _CHARS_PER_TOKEN

    total_text_chars = 0
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            total_text_chars += len(text)

    if total_text_chars <= char_budget:
        return result

    estimated_tokens = total_text_chars // _CHARS_PER_TOKEN
    label = f" from '{server_name}'" if server_name else ""
    marker = f"\n\n[MCP output{label} truncated: {estimated_tokens} tokens -> limit {max_tokens}]"

    remaining = char_budget
    truncated_any = False
    for block in content:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        if truncated_any:
            # Budget already exhausted by an earlier block; drop this text.
            block.text = ""
            continue
        if len(text) <= remaining:
            remaining -= len(text)
            continue
        block.text = text[:remaining] + marker
        remaining = 0
        truncated_any = True

    return result
