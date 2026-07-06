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

    def test_no_expiry_info_treated_as_expired(self):
        # A token that carries no usable expiry metadata must not be assumed
        # valid forever; it is treated as expired so the caller re-validates
        # (refresh when possible, else a fresh flow).
        tokens = {"access_token": "x"}
        assert MCPOAuthFlow._is_expired(tokens) is True

    def test_missing_obtained_at_treated_as_expired(self):
        tokens = {"access_token": "x", "expires_in": 3600}
        assert MCPOAuthFlow._is_expired(tokens) is True

    def test_missing_expires_in_treated_as_expired(self):
        tokens = {"access_token": "x", "obtained_at": time.time()}
        assert MCPOAuthFlow._is_expired(tokens) is True


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

    def test_cached_token_without_expiry_refreshes_when_possible(self, tmp_path, monkeypatch):
        # A cached token that lacks expiry metadata but has a refresh_token
        # must be refreshed rather than assumed valid forever.
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens(
            "no-expiry-refresh",
            {
                "access_token": "stale-token",
                "refresh_token": "ref-tok",
                "client_id": "cid",
            },
        )
        config = MCPOAuthConfig(client_id="cid")
        flow = MCPOAuthFlow("no-expiry-refresh", "https://example.com/mcp", config)

        with patch.object(flow, "refresh_token", new_callable=AsyncMock) as mock_refresh:
            mock_refresh.return_value = {"Authorization": "Bearer fresh-token"}
            headers = asyncio.run(flow.authenticate())
            mock_refresh.assert_awaited_once()
            assert headers == {"Authorization": "Bearer fresh-token"}

    def test_cached_token_without_expiry_no_refresh_starts_new_flow(self, tmp_path, monkeypatch):
        # No expiry AND no refresh_token: the stale token must NOT be returned;
        # instead the full flow is (attempted to be) started.
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens(
            "no-expiry-noref",
            {"access_token": "stale-token", "client_id": "cid"},
        )
        config = MCPOAuthConfig(client_id="cid")
        flow = MCPOAuthFlow("no-expiry-noref", "https://example.com/mcp", config)

        with patch.object(flow, "_discover_metadata", new_callable=AsyncMock) as mock_discover:
            mock_discover.side_effect = RuntimeError("new flow started")
            # The stale token must not be returned; a new flow is attempted.
            try:
                asyncio.run(flow.authenticate())
            except RuntimeError as exc:
                assert "new flow started" in str(exc)
            else:  # pragma: no cover - defensive
                raise AssertionError("stale token should not have been returned")
            mock_discover.assert_awaited_once()


# ---------------------------------------------------------------------------
# _OAuthCallbackHandler — CSRF/state validation & per-flow isolation
# ---------------------------------------------------------------------------


class TestCallbackStateValidation:
    @staticmethod
    def _bind(server, state, path="/callback"):
        server.oauth_expected_state = state
        server.oauth_callback_path = path

    @staticmethod
    def _get(port, path):
        import urllib.request

        with urllib.request.urlopen(  # noqa: S310 - loopback only
            f"http://127.0.0.1:{port}{path}"
        ) as resp:
            return resp.status, resp.read()

    def test_correct_state_accepted(self):
        server, port = _start_callback_server(None)
        self._bind(server, "good-state")
        try:
            status, _ = self._get(port, "/callback?code=the-code&state=good-state")
            assert status == 200
            assert server.oauth_result.auth_code == "the-code"
            assert server.oauth_result.error is None
        finally:
            server.shutdown()

    def test_wrong_state_rejected(self):
        server, port = _start_callback_server(None)
        self._bind(server, "expected-state")
        try:
            import urllib.error

            try:
                self._get(port, "/callback?code=evil-code&state=attacker-state")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
            else:  # pragma: no cover - defensive
                raise AssertionError("wrong state should be rejected with HTTP 400")
            # The forged code must NOT be captured.
            assert server.oauth_result.auth_code is None
            assert server.oauth_result.error == "state_mismatch"
        finally:
            server.shutdown()

    def test_missing_state_rejected(self):
        server, port = _start_callback_server(None)
        self._bind(server, "expected-state")
        try:
            import urllib.error

            try:
                self._get(port, "/callback?code=evil-code")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400
            else:  # pragma: no cover - defensive
                raise AssertionError("missing state should be rejected with HTTP 400")
            assert server.oauth_result.auth_code is None
            assert server.oauth_result.error == "state_mismatch"
        finally:
            server.shutdown()

    def test_concurrent_flows_are_isolated(self):
        # Two live servers (two flows) must not share the code/state that the
        # other captured — previously stored as handler CLASS attributes.
        server_a, port_a = _start_callback_server(None)
        server_b, port_b = _start_callback_server(None)
        self._bind(server_a, "state-a")
        self._bind(server_b, "state-b")
        try:
            self._get(port_a, "/callback?code=code-a&state=state-a")
            self._get(port_b, "/callback?code=code-b&state=state-b")
            assert server_a.oauth_result.auth_code == "code-a"
            assert server_b.oauth_result.auth_code == "code-b"
            assert server_a.oauth_result is not server_b.oauth_result
        finally:
            server_a.shutdown()
            server_b.shutdown()

    def test_wait_for_code_reads_per_flow_result(self):
        server, _port = _start_callback_server(None)
        server.oauth_result.auth_code = "captured"
        try:
            code = asyncio.run(MCPOAuthFlow._wait_for_code(server, timeout=1))
            assert code == "captured"
        finally:
            server.shutdown()

    def test_wait_for_code_raises_on_state_mismatch_error(self):
        server, _port = _start_callback_server(None)
        server.oauth_result.error = "state_mismatch"
        try:
            try:
                asyncio.run(MCPOAuthFlow._wait_for_code(server, timeout=1))
            except RuntimeError as exc:
                assert "state_mismatch" in str(exc)
            else:  # pragma: no cover - defensive
                raise AssertionError("expected RuntimeError on state mismatch")
        finally:
            server.shutdown()


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


# ---------------------------------------------------------------------------
# Dynamic client registration redirect URI handling
# ---------------------------------------------------------------------------


class TestDynamicRegistrationRedirectUri:
    METADATA = {
        "authorization_endpoint": "https://auth.example/authorize",
        "token_endpoint": "https://auth.example/token",
        "registration_endpoint": "https://auth.example/register",
    }

    @staticmethod
    def _flow(name: str) -> MCPOAuthFlow:
        # No client_id configured -> dynamic registration path
        return MCPOAuthFlow(name, "https://example.com/mcp", MCPOAuthConfig(scopes=["read"]))

    def test_registers_with_real_redirect_uri(self, tmp_path, monkeypatch):
        """The registered redirect_uri must match the actual callback URI."""
        monkeypatch.setenv("HOME", str(tmp_path))
        flow = self._flow("dyn-reg-server")
        captured: Dict[str, Any] = {}

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()

            async def fake_post(url, json=None, **kw):
                captured["url"] = url
                captured["payload"] = json
                resp = MagicMock()
                resp.status_code = 201
                resp.json.return_value = {"client_id": "dyn-client-id"}
                resp.raise_for_status = MagicMock()
                return resp

            mock_client.post = fake_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            client_id, secret = asyncio.run(
                flow._ensure_client(self.METADATA, "http://127.0.0.1:54321/callback")
            )

        assert client_id == "dyn-client-id"
        assert secret is None
        assert captured["payload"]["redirect_uris"] == ["http://127.0.0.1:54321/callback"]
        # No placeholder port-0 URI may ever be registered.
        assert "127.0.0.1:0" not in captured["payload"]["redirect_uris"][0]
        cached = load_tokens("dyn-reg-server")
        assert cached["client_id"] == "dyn-client-id"
        assert cached["redirect_uri"] == "http://127.0.0.1:54321/callback"

    def test_reuses_cached_registration_for_same_redirect_uri(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens(
            "dyn-cache-server",
            {"client_id": "cached-id", "redirect_uri": "http://127.0.0.1:7777/callback"},
        )
        flow = self._flow("dyn-cache-server")

        with patch("httpx.AsyncClient") as mock_cls:
            client_id, _ = asyncio.run(
                flow._ensure_client(self.METADATA, "http://127.0.0.1:7777/callback")
            )
            mock_cls.assert_not_called()

        assert client_id == "cached-id"

    def test_re_registers_when_redirect_uri_changes(self, tmp_path, monkeypatch):
        """A stale registration for another port must not be reused."""
        monkeypatch.setenv("HOME", str(tmp_path))
        save_tokens(
            "dyn-stale-server",
            {"client_id": "stale-id", "redirect_uri": "http://127.0.0.1:1111/callback"},
        )
        flow = self._flow("dyn-stale-server")

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()

            async def fake_post(url, json=None, **kw):
                resp = MagicMock()
                resp.json.return_value = {"client_id": "fresh-id"}
                resp.raise_for_status = MagicMock()
                return resp

            mock_client.post = fake_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            client_id, _ = asyncio.run(
                flow._ensure_client(self.METADATA, "http://127.0.0.1:2222/callback")
            )

        assert client_id == "fresh-id"
        cached = load_tokens("dyn-stale-server")
        assert cached["redirect_uri"] == "http://127.0.0.1:2222/callback"

    def test_missing_registration_endpoint_is_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        flow = self._flow("dyn-no-endpoint")
        metadata = {
            "authorization_endpoint": "https://auth.example/authorize",
            "token_endpoint": "https://auth.example/token",
        }
        try:
            asyncio.run(flow._ensure_client(metadata, "http://127.0.0.1:3333/callback"))
        except RuntimeError as exc:
            assert "registration_endpoint" in str(exc)
        else:
            raise AssertionError("expected RuntimeError for missing registration_endpoint")


class TestAuthenticateUsesActualCallbackPort:
    def test_full_flow_registers_actual_port(self, tmp_path, monkeypatch):
        """authenticate() must start the callback server before registration
        and use the same redirect URI for registration and authorization."""
        monkeypatch.setenv("HOME", str(tmp_path))
        flow = MCPOAuthFlow(
            "full-flow-server", "https://example.com/mcp", MCPOAuthConfig(scopes=["read"])
        )

        metadata = {
            "authorization_endpoint": "https://auth.example/authorize",
            "token_endpoint": "https://auth.example/token",
            "registration_endpoint": "https://auth.example/register",
        }
        seen: Dict[str, Any] = {}

        async def fake_discover():
            return metadata

        async def fake_ensure(meta, redirect_uri):
            seen["registration_redirect"] = redirect_uri
            return "cid", None

        async def fake_code_flow(meta, client_id, client_secret, *, server, redirect_uri):
            seen["authorization_redirect"] = redirect_uri
            assert server is not None
            return {"access_token": "tok", "obtained_at": time.time()}

        with (
            patch.object(flow, "_discover_metadata", side_effect=fake_discover),
            patch.object(flow, "_ensure_client", side_effect=fake_ensure),
            patch.object(flow, "_authorization_code_flow", side_effect=fake_code_flow),
        ):
            headers = asyncio.run(flow.authenticate())

        assert headers == {"Authorization": "Bearer tok"}
        assert seen["registration_redirect"] == seen["authorization_redirect"]
        assert "127.0.0.1:0" not in seen["registration_redirect"]
