"""OAuth 2.0 authentication flow for remote MCP servers.

Implements RFC 6749 Authorization Code Grant with PKCE (RFC 7636),
dynamic client registration (RFC 7591), and OAuth discovery via
``/.well-known/oauth-protected-resource`` and
``/.well-known/oauth-authorization-server``.

Token state is persisted under ``~/.koder/mcp-auth/<server-name>/``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Dict
from urllib.parse import urlencode, urlparse

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_AUTH_DIR_NAME = "mcp-auth"


@dataclass
class MCPOAuthConfig:
    """OAuth configuration for an MCP server."""

    client_id: str | None = None
    client_secret: str | None = None
    callback_port: int | None = None
    auth_server_metadata_url: str | None = None
    scopes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> MCPOAuthConfig | None:
        """Build from the ``oauth`` dict stored in ``MCPServerConfig``.

        Returns ``None`` when *data* is ``None`` or empty.
        """
        if not data:
            return None
        return cls(
            client_id=data.get("clientId") or data.get("client_id"),
            client_secret=data.get("clientSecret") or data.get("client_secret"),
            callback_port=data.get("callbackPort") or data.get("callback_port"),
            auth_server_metadata_url=(
                data.get("authServerMetadataUrl") or data.get("auth_server_metadata_url")
            ),
            scopes=data.get("scopes") or [],
        )


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------


def _token_dir(server_name: str) -> Path:
    return Path.home() / ".koder" / _AUTH_DIR_NAME / server_name


def _token_file(server_name: str) -> Path:
    return _token_dir(server_name) / "tokens.json"


def load_tokens(server_name: str) -> Dict[str, Any] | None:
    """Load persisted tokens for *server_name*, or ``None``."""
    path = _token_file(server_name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        logger.debug("Failed to read token file %s", path)
        return None


def save_tokens(server_name: str, tokens: Dict[str, Any]) -> None:
    """Persist *tokens* for *server_name*."""
    directory = _token_dir(server_name)
    directory.mkdir(parents=True, exist_ok=True)
    path = _token_file(server_name)
    path.write_text(json.dumps(tokens, indent=2), "utf-8")
    # Best-effort restrict permissions
    try:
        path.chmod(0o600)
    except OSError:
        pass


def clear_tokens(server_name: str) -> None:
    """Remove persisted tokens for *server_name*."""
    path = _token_file(server_name)
    if path.exists():
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Local callback HTTP server
# ---------------------------------------------------------------------------


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Tiny handler that captures the ``code`` query parameter."""

    auth_code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [None])[0]
        err = query.get("error", [None])[0]

        if code:
            _OAuthCallbackHandler.auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authorization successful.</h2>"
                b"<p>You can close this window and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            _OAuthCallbackHandler.error = err or "unknown_error"
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h2>Authorization failed: {err}</h2></body></html>".encode()
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug(format, *args)


def _start_callback_server(port: int | None) -> tuple[HTTPServer, int]:
    """Start a local HTTP server and return ``(server, actual_port)``."""
    bind_port = port or 0  # 0 = OS picks a free port
    server = HTTPServer(("127.0.0.1", bind_port), _OAuthCallbackHandler)
    actual_port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, actual_port


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def _generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------


class MCPOAuthFlow:
    """Performs a full OAuth 2.0 Authorization Code flow for an MCP server."""

    def __init__(
        self,
        server_name: str,
        server_url: str,
        config: MCPOAuthConfig,
    ) -> None:
        self.server_name = server_name
        self.server_url = server_url
        self.config = config
        self._metadata: Dict[str, Any] | None = None

    # -- public api ---------------------------------------------------------

    async def authenticate(self) -> Dict[str, str]:
        """Run the OAuth flow and return authorization headers.

        If valid cached tokens exist they are reused.  Expired tokens are
        refreshed automatically when a refresh token is available.
        """
        # 1. Check for cached tokens
        cached = load_tokens(self.server_name)
        if cached:
            if not self._is_expired(cached):
                return {"Authorization": f"Bearer {cached['access_token']}"}
            # Try refresh
            if cached.get("refresh_token"):
                try:
                    return await self.refresh_token()
                except Exception:
                    logger.debug("Token refresh failed; starting new flow")

        # 2. Discover metadata
        metadata = await self._discover_metadata()

        # 3. Dynamic client registration if needed
        client_id, client_secret = await self._ensure_client(metadata)

        # 4. Authorization code exchange
        tokens = await self._authorization_code_flow(metadata, client_id, client_secret)
        save_tokens(self.server_name, tokens)
        return {"Authorization": f"Bearer {tokens['access_token']}"}

    async def refresh_token(self) -> Dict[str, str]:
        """Refresh an expired access token and return new headers."""
        cached = load_tokens(self.server_name)
        if not cached or not cached.get("refresh_token"):
            raise RuntimeError("No refresh token available")

        metadata = await self._discover_metadata()
        token_endpoint = metadata["token_endpoint"]

        client_id = cached.get("client_id") or self.config.client_id
        client_secret = (
            cached.get("client_secret")
            or self.config.client_secret
            or os.environ.get("MCP_CLIENT_SECRET")
        )

        payload: Dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": cached["refresh_token"],
            "client_id": client_id,
        }
        if client_secret:
            payload["client_secret"] = client_secret

        async with httpx.AsyncClient() as client:
            resp = await client.post(token_endpoint, data=payload)
            resp.raise_for_status()
            token_data = resp.json()

        # Merge new tokens with old (server may not return a new refresh_token)
        merged = {**cached, **token_data}
        merged["obtained_at"] = time.time()
        save_tokens(self.server_name, merged)
        return {"Authorization": f"Bearer {merged['access_token']}"}

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _is_expired(tokens: Dict[str, Any]) -> bool:
        expires_in = tokens.get("expires_in")
        obtained_at = tokens.get("obtained_at")
        if expires_in is None or obtained_at is None:
            return False  # assume valid if we can't tell
        return time.time() > obtained_at + expires_in - 30  # 30s grace

    async def _discover_metadata(self) -> Dict[str, Any]:
        if self._metadata is not None:
            return self._metadata

        parsed = urlparse(self.server_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        urls_to_try = [
            f"{base}/.well-known/oauth-protected-resource",
            f"{base}/.well-known/oauth-authorization-server",
        ]
        if self.config.auth_server_metadata_url:
            urls_to_try.append(self.config.auth_server_metadata_url)

        async with httpx.AsyncClient(follow_redirects=True) as client:
            # First try the resource endpoint to get the authorization server
            for url in urls_to_try:
                try:
                    resp = await client.get(url, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        # If this is a protected resource document, it may
                        # point to the authorization server via
                        # ``authorization_servers``.
                        if "authorization_servers" in data and "token_endpoint" not in data:
                            auth_server_url = data["authorization_servers"][0]
                            meta_resp = await client.get(
                                f"{auth_server_url}/.well-known/oauth-authorization-server",
                                timeout=10,
                            )
                            if meta_resp.status_code == 200:
                                data = meta_resp.json()
                        self._metadata = data
                        return data
                except Exception:
                    continue

            # Fallback: try override URL directly
            if self.config.auth_server_metadata_url:
                try:
                    resp = await client.get(self.config.auth_server_metadata_url, timeout=10)
                    if resp.status_code == 200:
                        self._metadata = resp.json()
                        return self._metadata
                except Exception:
                    pass

        raise RuntimeError(
            f"Could not discover OAuth metadata for {self.server_url}. "
            f"Tried: {', '.join(urls_to_try)}"
        )

    async def _ensure_client(self, metadata: Dict[str, Any]) -> tuple[str, str | None]:
        """Return ``(client_id, client_secret)``.

        Uses pre-configured values when available, otherwise performs dynamic
        client registration (RFC 7591).
        """
        client_secret = self.config.client_secret or os.environ.get("MCP_CLIENT_SECRET")
        if self.config.client_id:
            return self.config.client_id, client_secret

        registration_endpoint = metadata.get("registration_endpoint")
        if not registration_endpoint:
            raise RuntimeError(
                "No client_id configured and server does not advertise a "
                "registration_endpoint for dynamic client registration."
            )

        callback_port = (
            self.config.callback_port or int(os.environ.get("MCP_OAUTH_CALLBACK_PORT", "0")) or None
        )
        redirect_uri = f"http://127.0.0.1:{callback_port or 0}/callback"
        # We don't know the actual port yet for dynamic registration when
        # callback_port is 0.  Best effort: register with a placeholder and
        # re-register later, or require callback_port to be set.
        if callback_port:
            redirect_uri = f"http://127.0.0.1:{callback_port}/callback"

        reg_payload: Dict[str, Any] = {
            "client_name": f"koder-mcp-{self.server_name}",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        if self.config.scopes:
            reg_payload["scope"] = " ".join(self.config.scopes)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                registration_endpoint,
                json=reg_payload,
                timeout=15,
            )
            resp.raise_for_status()
            reg_data = resp.json()

        registered_id = reg_data["client_id"]
        registered_secret = reg_data.get("client_secret")

        # Persist registration alongside tokens so we can reuse it
        cached = load_tokens(self.server_name) or {}
        cached["client_id"] = registered_id
        if registered_secret:
            cached["client_secret"] = registered_secret
        save_tokens(self.server_name, cached)

        return registered_id, registered_secret

    async def _authorization_code_flow(
        self,
        metadata: Dict[str, Any],
        client_id: str,
        client_secret: str | None,
    ) -> Dict[str, Any]:
        """Run the authorization code grant with PKCE."""
        authorization_endpoint = metadata.get("authorization_endpoint")
        token_endpoint = metadata.get("token_endpoint")
        if not authorization_endpoint or not token_endpoint:
            raise RuntimeError("OAuth metadata missing authorization_endpoint or token_endpoint")

        callback_port = (
            self.config.callback_port or int(os.environ.get("MCP_OAUTH_CALLBACK_PORT", "0")) or None
        )

        # Start local callback server
        server, actual_port = _start_callback_server(callback_port)
        redirect_uri = f"http://127.0.0.1:{actual_port}/callback"

        try:
            # PKCE
            code_verifier, code_challenge = _generate_pkce()
            state = secrets.token_urlsafe(32)

            # Reset handler state
            _OAuthCallbackHandler.auth_code = None
            _OAuthCallbackHandler.error = None

            # Build authorization URL
            auth_params: Dict[str, str] = {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
            scopes = self.config.scopes or metadata.get("scopes_supported", [])
            if scopes:
                auth_params["scope"] = " ".join(scopes)

            auth_url = f"{authorization_endpoint}?{urlencode(auth_params)}"

            # Open browser
            logger.info("Opening browser for OAuth authorization...")
            webbrowser.open(auth_url)

            # Wait for callback
            code = await self._wait_for_code(timeout=300)

            # Exchange code for tokens
            token_payload: Dict[str, str] = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            }
            if client_secret:
                token_payload["client_secret"] = client_secret

            async with httpx.AsyncClient() as http_client:
                resp = await http_client.post(token_endpoint, data=token_payload, timeout=15)
                resp.raise_for_status()
                token_data = resp.json()

            token_data["obtained_at"] = time.time()
            token_data["client_id"] = client_id
            if client_secret:
                token_data["client_secret"] = client_secret
            return token_data

        finally:
            server.shutdown()

    @staticmethod
    async def _wait_for_code(timeout: float = 300) -> str:
        """Poll until the callback handler has received the auth code."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _OAuthCallbackHandler.auth_code:
                return _OAuthCallbackHandler.auth_code
            if _OAuthCallbackHandler.error:
                raise RuntimeError(f"OAuth authorization failed: {_OAuthCallbackHandler.error}")
            await asyncio.sleep(0.25)
        raise TimeoutError("Timed out waiting for OAuth authorization callback")


# ---------------------------------------------------------------------------
# Convenience: resolve OAuth headers for a server config
# ---------------------------------------------------------------------------


async def resolve_oauth_headers(
    server_name: str,
    server_url: str,
    oauth_dict: Dict[str, Any] | None,
) -> Dict[str, str]:
    """Return OAuth ``Authorization`` headers for a server, or ``{}``."""
    config = MCPOAuthConfig.from_dict(oauth_dict)
    if config is None:
        return {}
    flow = MCPOAuthFlow(server_name, server_url, config)
    return await flow.authenticate()
