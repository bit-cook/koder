import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.config.service import RuntimeConfigService


def test_runtime_config_service_round_trips_schema(tmp_path):
    config_path = tmp_path / "config.yaml"
    service = RuntimeConfigService(config_path)
    config = service.load()
    config.harness.interactive_shell = "legacy"
    config.voice.provider = "google"
    config.voice.model = "gemini-2.5-flash"
    config.voice.api_version = "2025-04-01-preview"
    service.save(config)
    assert service.load().harness.interactive_shell == "legacy"
    assert service.load().voice.provider == "google"
    assert service.load().voice.model == "gemini-2.5-flash"
    assert service.load().voice.api_version == "2025-04-01-preview"


def test_runtime_config_service_migrates_legacy_harness_voice_fields(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "harness:\n  interactive_shell: runtime\n  permission_mode: default\n  voice_enabled: true\n  voice_provider: google\n",
        encoding="utf-8",
    )
    service = RuntimeConfigService(config_path)
    config = service.load()
    assert config.voice.enabled is True
    assert config.voice.provider == "google"


def test_runtime_config_service_accepts_bare_yaml_off_for_reasoning_display(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("harness:\n  reasoning_display: off\n", encoding="utf-8")

    service = RuntimeConfigService(config_path)

    assert service.load().harness.reasoning_display == "off"
