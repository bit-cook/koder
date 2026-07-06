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
        logger.debug("Failed to set token file permissions", exc_info=True)


def clear_tokens(server_name: str) -> None:
    """Remove persisted tokens for *server_name*."""
    path = _token_file(server_name)
    if path.exists():
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Local callback HTTP server
# ---------------------------------------------------------------------------


@dataclass
class _CallbackResult:
    """Per-flow container for the loopback callback outcome.

    Instances are bound to a single :class:`HTTPServer` so concurrent OAuth
    flows never share mutable state (previously stored as class attributes on
    the handler, which raced across flows).
    """

    auth_code: str | None = None
    error: str | None = None


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handler that captures the ``code`` query parameter for one flow.

    Expected ``state`` (CSRF token), the callback path, and the result
    container are read from the owning :class:`HTTPServer` instance so that
    each flow validates against its own generated state and writes into its
    own container. A request whose ``state`` does not match the value this
    flow generated is rejected without recording an auth code.
    """

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlparse

        result: _CallbackResult = getattr(self.server, "oauth_result", None) or _CallbackResult()
        expected_state: str | None = getattr(self.server, "oauth_expected_state", None)
        expected_path: str | None = getattr(self.server, "oauth_callback_path", None)

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        err = query.get("error", [None])[0]
        returned_state = query.get("state", [None])[0]

        # Reject callbacks on an unexpected path (ignore favicon/probes, etc.).
        if expected_path is not None and parsed.path != expected_path:
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Not found.</h2></body></html>")
            return

        # CSRF protection: the returned state MUST match the value this flow
        # generated. A missing or mismatched state is rejected outright so a
        # forged callback carrying an attacker-chosen ``code`` cannot be
        # accepted (RFC 6749 section 10.12).
        if expected_state is not None and returned_state != expected_state:
            result.error = "state_mismatch"
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authorization failed: state mismatch.</h2></body></html>"
            )
            return

        if code:
            result.auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authorization successful.</h2>"
                b"<p>You can close this window and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            result.error = err or "unknown_error"
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h2>Authorization failed: {err}</h2></body></html>".encode()
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        logger.debug(format, *args)


def _start_callback_server(port: int | None) -> tuple[HTTPServer, int]:
    """Start a local HTTP server and return ``(server, actual_port)``.

    The returned server carries a fresh :class:`_CallbackResult` on
    ``server.oauth_result``; the caller binds the expected ``state`` and
    callback path before opening the browser.
    """
    bind_port = port or 0  # 0 = OS picks a free port
    server = HTTPServer(("127.0.0.1", bind_port), _OAuthCallbackHandler)
    # Per-flow state lives on the server instance, not on the handler class,
    # so concurrent flows do not clobber each other.
    server.oauth_result = _CallbackResult()  # type: ignore[attr-defined]
    server.oauth_expected_state = None  # type: ignore[attr-defined]
    server.oauth_callback_path = None  # type: ignore[attr-defined]
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

        # 3. Start the local callback server first so dynamic client
        #    registration can use the real redirect URI. Servers that
        #    enforce exact redirect_uri matching (RFC 8252) reject
        #    authorization requests whose URI differs from registration.
        callback_port = self._resolve_callback_port()
        server, actual_port = _start_callback_server(callback_port)
        redirect_uri = f"http://127.0.0.1:{actual_port}/callback"

        try:
            # 4. Dynamic client registration if needed
            client_id, client_secret = await self._ensure_client(metadata, redirect_uri)

            # 5. Authorization code exchange
            tokens = await self._authorization_code_flow(
                metadata, client_id, client_secret, server=server, redirect_uri=redirect_uri
            )
        finally:
            server.shutdown()
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
            # No usable expiry metadata: do NOT assume infinite validity.
            # Treat the token as expired so the caller re-validates it —
            # refreshing when a refresh_token exists, otherwise re-running the
            # full authorization flow. This is conservative on purpose; a token
            # that carries expiry still uses the precise check below.
            return True
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
                    logger.debug("OAuth metadata discovery attempt failed", exc_info=True)
                    continue

            # Fallback: try override URL directly
            if self.config.auth_server_metadata_url:
                try:
                    resp = await client.get(self.config.auth_server_metadata_url, timeout=10)
                    if resp.status_code == 200:
                        self._metadata = resp.json()
                        return self._metadata
                except Exception:
                    logger.debug("OAuth metadata fallback URL fetch failed", exc_info=True)

        raise RuntimeError(
            f"Could not discover OAuth metadata for {self.server_url}. "
            f"Tried: {', '.join(urls_to_try)}"
        )

    def _resolve_callback_port(self) -> int | None:
        return (
            self.config.callback_port or int(os.environ.get("MCP_OAUTH_CALLBACK_PORT", "0")) or None
        )

    async def _ensure_client(
        self, metadata: Dict[str, Any], redirect_uri: str
    ) -> tuple[str, str | None]:
        """Return ``(client_id, client_secret)`` valid for *redirect_uri*.

        Uses pre-configured values when available. Otherwise reuses a cached
        dynamic registration when it was made for the same redirect URI, and
        performs dynamic client registration (RFC 7591) when not.
        """
        client_secret = self.config.client_secret or os.environ.get("MCP_CLIENT_SECRET")
        if self.config.client_id:
            return self.config.client_id, client_secret

        # Reuse a cached registration only when its redirect URI still
        # matches; a registration made for another port would be rejected by
        # servers that enforce exact redirect_uri matching.
        cached = load_tokens(self.server_name) or {}
        if cached.get("client_id") and cached.get("redirect_uri") == redirect_uri:
            return cached["client_id"], cached.get("client_secret")

        registration_endpoint = metadata.get("registration_endpoint")
        if not registration_endpoint:
            raise RuntimeError(
                "No client_id configured and server does not advertise a "
                "registration_endpoint for dynamic client registration."
            )

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

        # Persist registration (and its redirect URI) alongside tokens so a
        # fixed-port setup can reuse it on the next flow.
        cached["client_id"] = registered_id
        cached["redirect_uri"] = redirect_uri
        if registered_secret:
            cached["client_secret"] = registered_secret
        save_tokens(self.server_name, cached)

        return registered_id, registered_secret

    async def _authorization_code_flow(
        self,
        metadata: Dict[str, Any],
        client_id: str,
        client_secret: str | None,
        *,
        server: HTTPServer,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        """Run the authorization code grant with PKCE.

        The callback *server* is already listening on the port embedded in
        *redirect_uri*; the caller owns its shutdown.
        """
        authorization_endpoint = metadata.get("authorization_endpoint")
        token_endpoint = metadata.get("token_endpoint")
        if not authorization_endpoint or not token_endpoint:
            raise RuntimeError("OAuth metadata missing authorization_endpoint or token_endpoint")

        # PKCE
        code_verifier, code_challenge = _generate_pkce()
        state = secrets.token_urlsafe(32)

        # Bind the expected CSRF state and callback path to THIS server so the
        # handler validates the returned state and only this flow's result
        # container is written. State lives on the server instance (the fresh
        # ``oauth_result`` created by ``_start_callback_server``), not on the
        # handler class, so concurrent flows stay isolated.
        server.oauth_expected_state = state  # type: ignore[attr-defined]
        server.oauth_callback_path = urlparse(redirect_uri).path  # type: ignore[attr-defined]

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

        # Wait for callback (validates state via the handler)
        code = await self._wait_for_code(server, timeout=300)

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
        token_data["redirect_uri"] = redirect_uri
        if client_secret:
            token_data["client_secret"] = client_secret
        return token_data

    @staticmethod
    async def _wait_for_code(server: HTTPServer, timeout: float = 300) -> str:
        """Poll until this flow's callback handler has received the auth code.

        The result is read from the per-flow container bound to *server*, so a
        callback that failed state (CSRF) validation surfaces as an error and
        never yields a code.
        """
        result: _CallbackResult = getattr(server, "oauth_result", None) or _CallbackResult()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if result.auth_code:
                return result.auth_code
            if result.error:
                raise RuntimeError(f"OAuth authorization failed: {result.error}")
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
