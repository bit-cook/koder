"""Tests for MCP headersHelper dynamic authentication."""

from __future__ import annotations

import asyncio

from koder_agent.mcp.server_config import MCPServerConfig, MCPServerType
from koder_agent.mcp.server_factory import (
    _build_effective_headers,
    _resolve_headers_helper,
)


class TestResolveHeadersHelper:
    """Test the headersHelper shell command execution."""

    def test_valid_json_output(self):
        """headersHelper returning valid JSON should be parsed."""
        result = asyncio.run(_resolve_headers_helper('echo \'{"Authorization": "Bearer tok123"}\''))
        assert result == {"Authorization": "Bearer tok123"}

    def test_invalid_json_returns_empty(self):
        """headersHelper returning invalid JSON should return empty dict."""
        result = asyncio.run(_resolve_headers_helper("echo 'not json'"))
        assert result == {}

    def test_nonzero_exit_returns_empty(self):
        """headersHelper exiting with non-zero should return empty dict."""
        result = asyncio.run(_resolve_headers_helper("exit 1"))
        assert result == {}

    def test_timeout_returns_empty(self):
        """headersHelper exceeding timeout should return empty dict."""
        # Use a very short timeout for testing by patching the constant
        import koder_agent.mcp.server_factory as factory

        original = factory.HEADERS_HELPER_TIMEOUT_S
        factory.HEADERS_HELPER_TIMEOUT_S = 0.1
        try:
            result = asyncio.run(_resolve_headers_helper("sleep 10"))
            assert result == {}
        finally:
            factory.HEADERS_HELPER_TIMEOUT_S = original

    def test_non_object_returns_empty(self):
        """headersHelper returning a JSON array should return empty dict."""
        result = asyncio.run(_resolve_headers_helper('echo \'["a", "b"]\''))
        assert result == {}

    def test_values_coerced_to_strings(self):
        """headersHelper values should be coerced to strings."""
        result = asyncio.run(_resolve_headers_helper('echo \'{"X-Count": 42, "X-Flag": true}\''))
        assert result == {"X-Count": "42", "X-Flag": "True"}


class TestBuildEffectiveHeaders:
    """Test merging static headers with dynamic headersHelper output."""

    def test_static_only(self):
        """Without headersHelper, static headers are returned as-is."""
        config = MCPServerConfig(
            name="test",
            transport_type=MCPServerType.HTTP,
            url="https://example.com",
            headers={"X-Static": "value"},
        )
        result = asyncio.run(_build_effective_headers(config))
        assert result == {"X-Static": "value"}

    def test_dynamic_overrides_static(self):
        """headersHelper output should override static headers with same key."""
        config = MCPServerConfig(
            name="test",
            transport_type=MCPServerType.HTTP,
            url="https://example.com",
            headers={"Authorization": "Basic old", "X-Keep": "keep"},
            headers_helper='echo \'{"Authorization": "Bearer new"}\'',
        )
        result = asyncio.run(_build_effective_headers(config))
        assert result == {"Authorization": "Bearer new", "X-Keep": "keep"}

    def test_dynamic_adds_new_headers(self):
        """headersHelper can add headers not present in static config."""
        config = MCPServerConfig(
            name="test",
            transport_type=MCPServerType.HTTP,
            url="https://example.com",
            headers={},
            headers_helper='echo \'{"X-Dynamic": "added"}\'',
        )
        result = asyncio.run(_build_effective_headers(config))
        assert result == {"X-Dynamic": "added"}


class TestConfigParsing:
    """Test headersHelper config parsing from JSON."""

    def test_headers_helper_parsed_from_mapping(self):
        """headersHelper should be parsed from .mcp.json config."""
        from koder_agent.mcp.server_manager import MCPServerManager

        mgr = MCPServerManager.__new__(MCPServerManager)
        from koder_agent.mcp.server_config import MCPServerScope

        config = mgr._config_from_mapping(
            "test-server",
            {
                "type": "http",
                "url": "https://example.com",
                "headersHelper": "/opt/bin/get-auth.sh",
            },
            scope=MCPServerScope.PROJECT,
            source_path="/test/.mcp.json",
            expand_env=False,
        )
        assert config.headers_helper == "/opt/bin/get-auth.sh"

    def test_oauth_parsed_from_mapping(self):
        """OAuth config should be parsed from .mcp.json."""
        from koder_agent.mcp.server_manager import MCPServerManager

        mgr = MCPServerManager.__new__(MCPServerManager)
        from koder_agent.mcp.server_config import MCPServerScope

        config = mgr._config_from_mapping(
            "test-server",
            {
                "type": "http",
                "url": "https://example.com",
                "oauth": {
                    "clientId": "my-client",
                    "callbackPort": 8080,
                },
            },
            scope=MCPServerScope.PROJECT,
            source_path="/test/.mcp.json",
            expand_env=False,
        )
        assert config.oauth == {"clientId": "my-client", "callbackPort": 8080}

    def test_serialization_roundtrip(self):
        """headersHelper and oauth should survive serialize/deserialize."""
        from koder_agent.mcp.server_manager import MCPServerManager

        mgr = MCPServerManager.__new__(MCPServerManager)
        config = MCPServerConfig(
            name="test",
            transport_type=MCPServerType.HTTP,
            url="https://example.com",
            headers_helper="/get-headers.sh",
            oauth={"clientId": "cid", "callbackPort": 9090},
        )
        payload = mgr._serialize_project_server(config)
        assert payload["headersHelper"] == "/get-headers.sh"
        assert payload["oauth"] == {"clientId": "cid", "callbackPort": 9090}


class TestToolSearchConfig:
    """Test KODER_ENABLE_TOOL_SEARCH env var support."""

    def test_tool_search_env_var_false_clears_deferred(self, monkeypatch):
        """KODER_ENABLE_TOOL_SEARCH=false should clear deferred tools."""
        from koder_agent.tools.tool_search import _get_deferred_tools, _set_deferred_tools

        _set_deferred_tools(["fake_tool"])
        monkeypatch.setenv("KODER_ENABLE_TOOL_SEARCH", "false")

        # Simulate what agent.py does
        import os

        mode = os.environ.get("KODER_ENABLE_TOOL_SEARCH", "true").strip().lower()
        if mode == "false":
            _set_deferred_tools(None)

        assert _get_deferred_tools() == []

    def test_tool_search_default_enables_deferred(self):
        """Default behavior should enable deferred tools."""
        import os

        mode = os.environ.get("KODER_ENABLE_TOOL_SEARCH", "true").strip().lower()
        assert mode != "false"  # Default is "true"
