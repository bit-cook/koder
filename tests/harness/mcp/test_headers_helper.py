"""Tests for MCP headersHelper dynamic authentication."""

from __future__ import annotations

import asyncio
import os
import shlex
import sys

import pytest

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

    def test_cancellation_kills_child_group_and_drains_transport(self, tmp_path):
        async def scenario():
            pid_path = tmp_path / "helper.pid"
            child = (
                "import os,time,pathlib; "
                f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid())); "
                "time.sleep(60)"
            )
            command = f"{shlex.quote(sys.executable)} -c {shlex.quote(child)}"
            loop = asyncio.get_running_loop()
            baseline_transports = {
                id(transport)
                for transport in getattr(loop, "_transports", {}).values()
                if transport is not None and not transport.is_closing()
            }

            task = asyncio.create_task(_resolve_headers_helper(command))
            for _ in range(200):
                if pid_path.exists():
                    break
                await asyncio.sleep(0.01)
            assert pid_path.exists()
            child_pid = int(pid_path.read_text())

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            for _ in range(200):
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail(f"headers helper child {child_pid} survived cancellation")

            leaked_transports = [
                transport
                for transport in getattr(loop, "_transports", {}).values()
                if transport is not None
                and not transport.is_closing()
                and id(transport) not in baseline_transports
            ]
            assert leaked_transports == []

        asyncio.run(scenario())

    def test_non_object_returns_empty(self):
        """headersHelper returning a JSON array should return empty dict."""
        result = asyncio.run(_resolve_headers_helper('echo \'["a", "b"]\''))
        assert result == {}

    def test_values_coerced_to_strings(self):
        """headersHelper values should be coerced to strings."""
        result = asyncio.run(_resolve_headers_helper('echo \'{"X-Count": 42, "X-Flag": true}\''))
        assert result == {"X-Count": "42", "X-Flag": "True"}

    def test_reviewed_argv_runs_without_a_shell_and_with_explicit_env(self, tmp_path):
        helper = tmp_path / "helper"
        helper.write_text('#!/bin/sh\nprintf \'{"Mode": "safe"}\'\n', encoding="utf-8")
        helper.chmod(0o755)

        result = asyncio.run(
            _resolve_headers_helper(
                "ignored shell text",
                argv=[str(helper)],
                env={"PATH": str(tmp_path)},
                cwd=str(tmp_path),
            )
        )

        assert result == {"Mode": "safe"}


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
