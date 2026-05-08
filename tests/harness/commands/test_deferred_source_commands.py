import asyncio
from types import SimpleNamespace

from koder_agent.harness.commands.interactive import HarnessInteractiveCommandHandler
from koder_agent.harness.config.service import RuntimeConfigService
from koder_agent.harness.voice.service import VoiceDictationError


def _run(command: str, *, handler: HarnessInteractiveCommandHandler) -> str:
    return asyncio.run(handler.handle_slash_input(command, scheduler=None))


def _config(provider: str):
    return SimpleNamespace(model=SimpleNamespace(provider=provider))


def test_voice_requires_provider_credentials_for_supported_oauth_provider(tmp_path, monkeypatch):
    handler = HarnessInteractiveCommandHandler(
        config_service=RuntimeConfigService(tmp_path / "config.yaml")
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config", lambda: _config("google")
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.resolve_voice_credentials",
        lambda _provider: (_ for _ in ()).throw(VoiceDictationError("missing credentials")),
    )

    output = _run("/voice", handler=handler)

    assert "Voice mode requires credentials for provider 'google'." in output
    assert "koder auth login google" in output


def test_voice_reports_unsupported_provider_even_with_credentials(tmp_path, monkeypatch):
    handler = HarnessInteractiveCommandHandler(
        config_service=RuntimeConfigService(tmp_path / "config.yaml")
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config", lambda: _config("claude")
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.resolve_voice_credentials",
        lambda _provider: ("oauth-token", {}, None),
    )

    output = _run("/voice", handler=handler)

    assert output == "Voice mode is not available for provider: claude."


def test_voice_toggles_using_provider_backed_credentials(tmp_path, monkeypatch):
    config_service = RuntimeConfigService(tmp_path / "config.yaml")
    handler = HarnessInteractiveCommandHandler(config_service=config_service)
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config", lambda: _config("chatgpt")
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.resolve_voice_credentials",
        lambda _provider: ("oauth-token", {}, None),
    )

    enabled = _run("/voice", handler=handler)
    disabled = _run("/voice", handler=handler)

    assert "Voice mode enabled." in enabled
    assert "provider: chatgpt" in enabled
    assert disabled == "Voice mode disabled."
    assert config_service.load().voice.enabled is False


def test_voice_provider_override_can_be_set_and_reported(tmp_path, monkeypatch):
    config_service = RuntimeConfigService(tmp_path / "config.yaml")
    handler = HarnessInteractiveCommandHandler(config_service=config_service)
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.get_config", lambda: _config("openai")
    )
    monkeypatch.setattr(
        "koder_agent.harness.commands.interactive.resolve_voice_credentials",
        lambda _provider: ("oauth-token", {}, None),
    )

    set_output = _run("/voice provider google", handler=handler)
    status_output = _run("/voice status", handler=handler)

    assert set_output == "Voice provider set to: google"
    assert "voice_enabled: False" in status_output
    assert "voice_provider: google" in status_output
    assert "voice_model: None" in status_output
    assert "voice_api_version: None" in status_output
    assert "effective_provider: google" in status_output
