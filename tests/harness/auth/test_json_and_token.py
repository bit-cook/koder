from __future__ import annotations

import argparse
import io
import json
import time

import pytest

from koder_agent.auth.base import OAuthTokens
from koder_agent.harness.auth import commands


class FakeStorage:
    def __init__(self):
        self.saved: dict[str, OAuthTokens] = {}

    def save(self, tokens: OAuthTokens) -> None:
        self.saved[tokens.provider] = tokens

    def load(self, provider: str):
        return self.saved.get(provider)

    def get_all_tokens(self):
        return dict(self.saved)


def _future_tokens(provider="chatgpt"):
    return OAuthTokens(
        provider=provider,
        access_token="access-secret-value",
        refresh_token="refresh-secret-value",
        expires_at=int(time.time() * 1000) + 3_600_000,
        email="user@example.com",
        models=["gpt-4.1", "gpt-4o"],
    )


def test_build_token_status_dict_redacts_tokens():
    payload = commands._build_token_status_dict("chatgpt", _future_tokens())
    assert payload["provider"] == "chatgpt"
    assert payload["status"] == "valid"
    assert payload["account"] == "user@example.com"
    assert payload["has_access_token"] is True
    assert payload["has_refresh_token"] is True
    # Raw secret values must never appear in the serialized dict.
    serialized = json.dumps(payload)
    assert "access-secret-value" not in serialized
    assert "refresh-secret-value" not in serialized


@pytest.mark.asyncio
async def test_handle_status_json_all_providers(monkeypatch, capsys):
    storage = FakeStorage()
    storage.save(_future_tokens("chatgpt"))
    monkeypatch.setattr(commands, "get_token_storage", lambda: storage)

    await commands.handle_status_json(None)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["providers"][0]["provider"] == "chatgpt"
    assert payload["providers"][0]["status"] == "valid"


@pytest.mark.asyncio
async def test_handle_status_json_missing_provider(monkeypatch, capsys):
    storage = FakeStorage()
    monkeypatch.setattr(commands, "get_token_storage", lambda: storage)

    await commands.handle_status_json("claude")
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["providers"][0] == {"provider": "claude", "status": "not_configured"}


def test_handle_token_login_saves(monkeypatch, capsys):
    storage = FakeStorage()
    monkeypatch.setattr(commands, "get_token_storage", lambda: storage)

    ok = commands.handle_token_login("claude", "raw-token")
    assert ok is True
    assert "claude" in storage.saved
    assert storage.saved["claude"].access_token == "raw-token"
    assert storage.saved["claude"].expires_at > int(time.time() * 1000)


def test_handle_token_login_rejects_copilot(monkeypatch, capsys):
    storage = FakeStorage()
    monkeypatch.setattr(commands, "get_token_storage", lambda: storage)
    ok = commands.handle_token_login("github_copilot", "raw-token")
    assert ok is False
    assert storage.saved == {}


def test_resolve_ingested_token_stdin(monkeypatch):
    monkeypatch.setattr(commands.sys, "stdin", io.StringIO("  stdin-token\n"))
    monkeypatch.delenv(commands.AUTH_TOKEN_ENV, raising=False)
    assert commands._resolve_ingested_token("-") == "stdin-token"


def test_resolve_ingested_token_literal(monkeypatch):
    monkeypatch.delenv(commands.AUTH_TOKEN_ENV, raising=False)
    assert commands._resolve_ingested_token("literal") == "literal"


def test_resolve_ingested_token_env_fallback(monkeypatch):
    monkeypatch.setenv(commands.AUTH_TOKEN_ENV, "env-token")
    assert commands._resolve_ingested_token(None) == "env-token"


def test_resolve_ingested_token_none(monkeypatch):
    monkeypatch.delenv(commands.AUTH_TOKEN_ENV, raising=False)
    assert commands._resolve_ingested_token(None) is None


@pytest.mark.asyncio
async def test_handle_auth_subcommand_status_json(monkeypatch, capsys):
    storage = FakeStorage()
    storage.save(_future_tokens("chatgpt"))
    monkeypatch.setattr(commands, "get_token_storage", lambda: storage)

    args = argparse.Namespace(auth_command="status", provider=None, json_output=True)
    exit_code = await commands.handle_auth_subcommand(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["providers"][0]["provider"] == "chatgpt"


@pytest.mark.asyncio
async def test_handle_auth_subcommand_login_token_stdin(monkeypatch, capsys):
    storage = FakeStorage()
    monkeypatch.setattr(commands, "get_token_storage", lambda: storage)
    monkeypatch.setattr(commands.sys, "stdin", io.StringIO("piped-token\n"))
    monkeypatch.delenv(commands.AUTH_TOKEN_ENV, raising=False)

    args = argparse.Namespace(auth_command="login", provider="claude", timeout=300, token="-")
    exit_code = await commands.handle_auth_subcommand(args)
    assert exit_code == 0
    assert storage.saved["claude"].access_token == "piped-token"


@pytest.mark.asyncio
async def test_handle_auth_subcommand_login_token_env(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(commands, "get_token_storage", lambda: storage)
    monkeypatch.setenv(commands.AUTH_TOKEN_ENV, "env-token")

    args = argparse.Namespace(auth_command="login", provider="claude", timeout=300, token=None)
    exit_code = await commands.handle_auth_subcommand(args)
    assert exit_code == 0
    assert storage.saved["claude"].access_token == "env-token"


@pytest.mark.asyncio
async def test_handle_auth_subcommand_login_normal_flow_when_no_token(monkeypatch):
    calls = {"login": 0}

    async def fake_login(provider, timeout):
        calls["login"] += 1
        return True

    monkeypatch.setattr(commands, "handle_login", fake_login)
    monkeypatch.delenv(commands.AUTH_TOKEN_ENV, raising=False)

    args = argparse.Namespace(auth_command="login", provider="claude", timeout=300, token=None)
    exit_code = await commands.handle_auth_subcommand(args)
    assert exit_code == 0
    assert calls["login"] == 1
