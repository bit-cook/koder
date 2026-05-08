import asyncio
from types import SimpleNamespace

from koder_agent.harness.voice.service import (
    VoiceDictationController,
    _resolve_azure_endpoint_and_api_version,
    resolve_voice_model,
    resolve_voice_provider,
    should_start_voice_shortcut,
)


def _runtime_config(*, enabled=False, provider=None, model=None, base_url=None, api_version=None):
    voice = SimpleNamespace(
        enabled=enabled,
        provider=provider,
        model=model,
        api_key=None,
        base_url=base_url,
        api_version=api_version,
    )
    model_cfg = SimpleNamespace(
        provider="openai", name="gpt-5.4", azure_api_version=None, base_url=None
    )
    return SimpleNamespace(voice=voice, model=model_cfg)


def test_resolve_voice_provider_prefers_explicit_voice_provider():
    config = _runtime_config(provider="google")
    assert resolve_voice_provider(config, "openai") == "google"


def test_resolve_voice_provider_falls_back_to_model_provider():
    config = _runtime_config(provider=None)
    assert resolve_voice_provider(config, "chatgpt") == "chatgpt"


def test_resolve_voice_model_prefers_explicit_voice_model():
    config = _runtime_config(provider="azure", model="my-transcribe-deployment")
    assert resolve_voice_model(config, "azure") == "my-transcribe-deployment"


def test_resolve_azure_endpoint_and_api_version_from_voice_config():
    config = _runtime_config(
        provider="azure",
        base_url="https://demo.openai.azure.com/openai/deployments/transcribe",
        api_version="2025-04-01-preview",
    )
    endpoint, api_version = _resolve_azure_endpoint_and_api_version(config)
    assert endpoint == "https://demo.openai.azure.com"
    assert api_version == "2025-04-01-preview"


def test_double_space_shortcut_triggers_only_from_empty_prompt():
    assert (
        should_start_voice_shortcut(
            buffer_text=" ",
            cursor_position=1,
            last_space_at=10.0,
            now=10.2,
            enabled=True,
            busy=False,
        )
        is True
    )
    assert (
        should_start_voice_shortcut(
            buffer_text="hello ",
            cursor_position=6,
            last_space_at=10.0,
            now=10.1,
            enabled=True,
            busy=False,
        )
        is False
    )


def test_double_space_shortcut_respects_disabled_and_busy_state():
    assert (
        should_start_voice_shortcut(
            buffer_text=" ",
            cursor_position=1,
            last_space_at=10.0,
            now=10.1,
            enabled=False,
            busy=False,
        )
        is False
    )
    assert (
        should_start_voice_shortcut(
            buffer_text=" ",
            cursor_position=1,
            last_space_at=10.0,
            now=10.1,
            enabled=True,
            busy=True,
        )
        is False
    )


def test_voice_controller_records_transcribes_and_returns_final_text():
    events = []

    class _Recorder:
        def __init__(self):
            self.started = False

        def start(self):
            self.started = True

        def stop(self):
            return b"audio-bytes"

        def cancel(self):
            self.started = False

    class _Transcriber:
        async def transcribe(self, *, audio_bytes, provider, on_partial=None):
            assert audio_bytes == b"audio-bytes"
            assert provider == "openai"
            if on_partial:
                on_partial("partial transcript")
            return "final transcript"

    controller = VoiceDictationController(
        config_getter=lambda: _runtime_config(enabled=True, provider="openai"),
        model_provider_getter=lambda: "chatgpt",
        recorder_factory=_Recorder,
        transcriber=_Transcriber(),
    )

    asyncio.run(controller.start_recording(on_status=events.append))
    result = asyncio.run(
        controller.stop_recording(
            on_status=events.append,
            on_partial=lambda text: events.append(f"partial:{text}"),
        )
    )

    assert result == "final transcript"
    assert events == ["recording", "transcribing", "partial:partial transcript", None]


def test_voice_controller_cancel_resets_state():
    class _Recorder:
        def start(self):
            return None

        def stop(self):
            return b"audio-bytes"

        def cancel(self):
            return None

    controller = VoiceDictationController(
        config_getter=lambda: _runtime_config(enabled=True, provider="openai"),
        model_provider_getter=lambda: "openai",
        recorder_factory=_Recorder,
        transcriber=SimpleNamespace(),
    )

    asyncio.run(controller.start_recording(on_status=lambda _value: None))
    controller.cancel()
    assert controller.is_busy is False
