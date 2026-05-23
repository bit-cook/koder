import pytest

from koder_agent.harness.auth import commands


@pytest.mark.asyncio
async def test_handle_login_accepts_github_copilot_alias(monkeypatch):
    calls = []

    async def fake_login(*, timeout):
        calls.append(timeout)
        return True

    monkeypatch.setattr(commands, "handle_github_copilot_login", fake_login)

    assert await commands.handle_login("github-copilot", timeout=12) is True
    assert calls == [12]


@pytest.mark.asyncio
async def test_handle_login_normalizes_copilot_alias(monkeypatch):
    calls = []

    async def fake_login(*, timeout):
        calls.append(timeout)
        return True

    monkeypatch.setattr(commands, "handle_github_copilot_login", fake_login)

    assert await commands.handle_login("copilot", timeout=30) is True
    assert calls == [30]


@pytest.mark.asyncio
async def test_github_copilot_status_handles_non_numeric_expiry(monkeypatch, tmp_path, capsys):
    token_dir = tmp_path / "copilot"
    token_dir.mkdir()
    access_token = token_dir / "access-token"
    api_key = token_dir / "api-key.json"
    access_token.write_text("token", encoding="utf-8")
    api_key.write_text('{"expires_at": "not-a-number"}', encoding="utf-8")

    class FakeAuthenticator:
        def __init__(self):
            self.token_dir = str(token_dir)
            self.access_token_file = str(access_token)
            self.api_key_file = str(api_key)

    monkeypatch.setattr(
        "litellm.llms.github_copilot.authenticator.Authenticator",
        FakeAuthenticator,
    )

    await commands._print_github_copilot_status()

    output = capsys.readouterr().out
    assert "github_copilot" in output
    assert "Expires: unavailable" in output
