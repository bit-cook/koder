"""Tests for MCP OAuth authentication flow (koder_agent.mcp.oauth)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

from koder_agent.mcp.oauth import (
    MCPOAuthConfig,
    MCPOAuthFlow,
    _start_callback_server,
    clear_tokens,
    load_tokens,
    resolve_oauth_headers,
    save_tokens,
)

# ---------------------------------------------------------------------------
# MCPOAuthConfig
# ---------------------------------------------------------------------------


class TestMCPOAuthConfig:
    def test_from_dict_none_returns_none(self):
        assert MCPOAuthConfig.from_dict(None) is None

    def test_from_dict_empty_returns_none(self):
        assert MCPOAuthConfig.from_dict({}) is None

    def test_from_dict_camel_case_keys(self):
        cfg = MCPOAuthConfig.from_dict(
            {
                "clientId": "my-id",
                "clientSecret": "my-secret",
                "callbackPort": 9999,
                "authServerMetadataUrl": "https://auth.example/.well-known/openid",
                "scopes": ["read", "write"],
            }
        )
        assert cfg is not None
        assert cfg.client_id == "my-id"
        assert cfg.client_secret == "my-secret"
        assert cfg.callback_port == 9999
        assert cfg.auth_server_metadata_url == "https://auth.example/.well-known/openid"
        assert cfg.scopes == ["read", "write"]

    def test_from_dict_snake_case_keys(self):
        cfg = MCPOAuthConfig.from_dict(
            {
                "client_id": "snake-id",
                "client_secret": "snake-secret",
                "callback_port": 8888,
                "auth_server_metadata_url": "https://auth2.example/meta",
            }
        )
        assert cfg is not None
        assert cfg.client_id == "snake-id"
        assert cfg.client_secret == "snake-secret"
        assert cfg.callback_port == 8888
        assert cfg.auth_server_metadata_url == "https://auth2.example/meta"

    def test_from_dict_defaults(self):
        cfg = MCPOAuthConfig.from_dict({"clientId": "only-id"})
        assert cfg is not None
        assert cfg.client_id == "only-id"
        assert cfg.client_secret is None
        assert cfg.callback_port is None
        assert cfg.auth_server_metadata_url is None
        assert cfg.scopes == []


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------


class TestTokenPersistence:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        tokens = {
            "access_token": "abc123",
            "refresh_token": "ref456",
            "expires_in": 3600,
            "obtained_at": time.time(),
        }
        save_tokens("test-server", tokens)
        loaded = load_tokens("test-server")
        assert loaded is not None
        assert loaded["access_token"] == "abc123"
        assert loaded["refresh_token"] == "ref456"

    def test_load_nonexistent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        assert load_tokens("nonexistent") is None

    def test_clear_tokens(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens("deleteme", {"access_token": "x"})
        assert load_tokens("deleteme") is not None
        clear_tokens("deleteme")
        assert load_tokens("deleteme") is None

    def test_clear_nonexistent_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        clear_tokens("never-saved")  # should not raise

    def test_token_file_permissions(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens("perm-test", {"access_token": "secret"})
        token_path = tmp_path / ".koder" / "mcp-auth" / "perm-test" / "tokens.json"
        assert token_path.exists()
        mode = token_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_token_dir_under_koder(self, tmp_path, monkeypatch):
        """Tokens must live under ~/.koder/mcp-auth/."""
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens("dir-check", {"access_token": "t"})
        assert (tmp_path / ".koder" / "mcp-auth" / "dir-check" / "tokens.json").exists()
        assert sorted(path.name for path in tmp_path.iterdir()) == [".koder"]


# ---------------------------------------------------------------------------
# Callback server
# ---------------------------------------------------------------------------


class TestCallbackServer:
    def test_start_callback_server_picks_free_port(self):
        server, port = _start_callback_server(None)
        assert port > 0
        server.shutdown()

    def test_start_callback_server_uses_specified_port(self):
        server, port = _start_callback_server(0)
        assert port > 0
        server.shutdown()


# ---------------------------------------------------------------------------
# MCPOAuthFlow._is_expired
# ---------------------------------------------------------------------------


class TestTokenExpiry:
    def test_not_expired(self):
        tokens = {
            "access_token": "x",
            "expires_in": 3600,
            "obtained_at": time.time(),
        }
        assert MCPOAuthFlow._is_expired(tokens) is False

    def test_expired(self):
        tokens = {
            "access_token": "x",
            "expires_in": 3600,
            "obtained_at": time.time() - 4000,
        }
        assert MCPOAuthFlow._is_expired(tokens) is True

    def test_no_expiry_info_not_expired(self):
        tokens = {"access_token": "x"}
        assert MCPOAuthFlow._is_expired(tokens) is False


# ---------------------------------------------------------------------------
# MCPOAuthFlow.authenticate — cached token path
# ---------------------------------------------------------------------------


class TestAuthenticateCachedTokens:
    def test_returns_cached_valid_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens(
            "cached-server",
            {
                "access_token": "cached-token",
                "expires_in": 3600,
                "obtained_at": time.time(),
            },
        )
        config = MCPOAuthConfig(client_id="cid")
        flow = MCPOAuthFlow("cached-server", "https://example.com/mcp", config)
        headers = asyncio.run(flow.authenticate())
        assert headers == {"Authorization": "Bearer cached-token"}

    def test_expired_token_triggers_refresh(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens(
            "expired-server",
            {
                "access_token": "old-token",
                "refresh_token": "ref-tok",
                "expires_in": 3600,
                "obtained_at": time.time() - 4000,
                "client_id": "cid",
            },
        )
        config = MCPOAuthConfig(client_id="cid")
        flow = MCPOAuthFlow("expired-server", "https://example.com/mcp", config)

        with patch.object(flow, "refresh_token", new_callable=AsyncMock) as mock_refresh:
            mock_refresh.return_value = {"Authorization": "Bearer refreshed-token"}
            headers = asyncio.run(flow.authenticate())
            mock_refresh.assert_awaited_once()
            assert headers == {"Authorization": "Bearer refreshed-token"}


# ---------------------------------------------------------------------------
# MCPOAuthFlow._discover_metadata
# ---------------------------------------------------------------------------


class TestDiscoverMetadata:
    def test_discovers_from_well_known(self, monkeypatch):
        metadata = {
            "authorization_endpoint": "https://auth.example/authorize",
            "token_endpoint": "https://auth.example/token",
        }

        async def mock_get(self, url, **kwargs):
            resp = MagicMock()
            if "oauth-protected-resource" in url:
                resp.status_code = 200
                resp.json.return_value = metadata
            else:
                resp.status_code = 404
            return resp

        config = MCPOAuthConfig()
        flow = MCPOAuthFlow("disc-server", "https://example.com/mcp", config)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=lambda url, **kw: _make_resp(url, metadata))
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = asyncio.run(flow._discover_metadata())
            assert result["authorization_endpoint"] == "https://auth.example/authorize"

    def test_uses_override_url(self):
        metadata = {
            "authorization_endpoint": "https://custom.auth/authorize",
            "token_endpoint": "https://custom.auth/token",
        }
        config = MCPOAuthConfig(auth_server_metadata_url="https://custom.auth/.well-known/openid")
        flow = MCPOAuthFlow("override-server", "https://example.com/mcp", config)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            call_count = 0

            async def smart_get(url, **kw):
                nonlocal call_count
                call_count += 1
                resp = MagicMock()
                if "custom.auth" in url:
                    resp.status_code = 200
                    resp.json.return_value = metadata
                else:
                    resp.status_code = 404
                return resp

            mock_client.get = smart_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = asyncio.run(flow._discover_metadata())
            assert result["token_endpoint"] == "https://custom.auth/token"


# ---------------------------------------------------------------------------
# resolve_oauth_headers convenience
# ---------------------------------------------------------------------------


class TestResolveOauthHeaders:
    def test_returns_empty_when_no_oauth(self):
        result = asyncio.run(resolve_oauth_headers("server", "https://example.com", None))
        assert result == {}

    def test_returns_empty_for_empty_dict(self):
        result = asyncio.run(resolve_oauth_headers("server", "https://example.com", {}))
        assert result == {}

    def test_uses_cached_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens(
            "resolve-server",
            {
                "access_token": "resolve-tok",
                "expires_in": 3600,
                "obtained_at": time.time(),
            },
        )
        result = asyncio.run(
            resolve_oauth_headers(
                "resolve-server",
                "https://example.com/mcp",
                {"clientId": "cid"},
            )
        )
        assert result == {"Authorization": "Bearer resolve-tok"}


# ---------------------------------------------------------------------------
# Integration with _build_effective_headers
# ---------------------------------------------------------------------------


class TestBuildEffectiveHeadersIntegration:
    def test_oauth_headers_merged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens(
            "factory-test",
            {
                "access_token": "factory-tok",
                "expires_in": 3600,
                "obtained_at": time.time(),
            },
        )

        from koder_agent.mcp.server_config import MCPServerConfig, MCPServerType
        from koder_agent.mcp.server_factory import _build_effective_headers

        config = MCPServerConfig(
            name="factory-test",
            transport_type=MCPServerType.HTTP,
            url="https://example.com/mcp",
            headers={"X-Custom": "val"},
            oauth={"clientId": "cid"},
        )
        headers = asyncio.run(_build_effective_headers(config))
        assert headers["X-Custom"] == "val"
        assert headers["Authorization"] == "Bearer factory-tok"

    def test_no_oauth_no_change(self):
        from koder_agent.mcp.server_config import MCPServerConfig, MCPServerType
        from koder_agent.mcp.server_factory import _build_effective_headers

        config = MCPServerConfig(
            name="no-oauth",
            transport_type=MCPServerType.HTTP,
            url="https://example.com/mcp",
            headers={"X-Only": "static"},
        )
        headers = asyncio.run(_build_effective_headers(config))
        assert headers == {"X-Only": "static"}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_resp(url: str, metadata: Dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    if "oauth-protected-resource" in url:
        resp.status_code = 200
        resp.json.return_value = metadata
    else:
        resp.status_code = 404
    return resp
