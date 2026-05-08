import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from koder_agent.config import reset_config_manager
from koder_agent.config.manager import ConfigManager
from koder_agent.utils.client import llm_completion


def _write_config(tmp_path, data: dict) -> None:
    config_dir = tmp_path / ".koder"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path):
    config_path = Path(tmp_path) / ".koder" / "config.yaml"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.delenv("KODER_MODEL", raising=False)
    monkeypatch.delenv("KODER_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    reset_config_manager()
    yield
    reset_config_manager()


def test_llm_completion_uses_aresponses_for_copilot_codex(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-5.1-codex", "provider": "github_copilot"}},
    )
    monkeypatch.setenv("GITHUB_TOKEN", "gh-test")

    calls = {"aresponses": 0, "acompletion": 0}

    async def fake_aresponses(**kwargs):
        calls["aresponses"] += 1
        return {
            "id": "resp_123",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
        }

    async def fake_acompletion(**kwargs):
        calls["acompletion"] += 1
        raise AssertionError("acompletion should not be called for copilot codex")

    import koder_agent.utils.client as client_mod

    # In some test environments litellm may be stubbed without these attrs.
    monkeypatch.setattr(client_mod.litellm, "aresponses", fake_aresponses, raising=False)
    monkeypatch.setattr(client_mod.litellm, "acompletion", fake_acompletion, raising=False)

    text = asyncio.run(
        llm_completion(
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    assert text == "ok"
    assert calls["aresponses"] == 1


def test_llm_completion_uses_override_provider_credentials_and_base_url(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4.1", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://anthropic.example.local")

    captured: dict[str, object] = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    import koder_agent.utils.client as client_mod

    monkeypatch.setattr(client_mod.litellm, "acompletion", fake_acompletion, raising=False)

    text = asyncio.run(
        llm_completion(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-opus-4-1",
        )
    )
    assert text == "ok"
    assert str(captured["model"]).endswith("anthropic/claude-opus-4-1")
    assert captured["api_key"] == "sk-anthropic"
    assert captured["base_url"] == "https://anthropic.example.local"


def test_llm_completion_prefers_koder_base_url(monkeypatch, tmp_path):
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-4.1", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example.local")
    monkeypatch.setenv("KODER_BASE_URL", "https://koder.example.local/v1")

    captured: dict[str, object] = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    import koder_agent.utils.client as client_mod

    monkeypatch.setattr(client_mod.litellm, "acompletion", fake_acompletion, raising=False)

    text = asyncio.run(llm_completion(messages=[{"role": "user", "content": "hi"}]))

    assert text == "ok"
    assert captured["base_url"] == "https://koder.example.local/v1"


def test_llm_completion_prefixes_native_openai_model_for_litellm(monkeypatch, tmp_path):
    """Newer OpenAI models (e.g. gpt-5.4) must be prefixed with openai/ for litellm."""
    _write_config(
        tmp_path,
        {"model": {"name": "gpt-5.4", "provider": "openai"}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    captured: dict[str, object] = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    import koder_agent.utils.client as client_mod

    monkeypatch.setattr(client_mod.litellm, "acompletion", fake_acompletion, raising=False)

    text = asyncio.run(
        llm_completion(
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    assert text == "ok"
    assert captured["model"] == "openai/gpt-5.4"
    assert captured["api_key"] == "sk-test"
