import sys
import types
from pathlib import Path

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.config.schema import HarnessRuntimeConfig, RuntimeConfig
from koder_agent.harness.config.service import RuntimeConfigService


def test_runtime_config_service_round_trips_schema(tmp_path):
    config_path = tmp_path / "config.yaml"
    service = RuntimeConfigService(config_path)
    config = service.load()
    config.harness.permission_mode = "bypass"
    config.voice.provider = "google"
    config.voice.model = "gemini-2.5-flash"
    config.voice.api_version = "2025-04-01-preview"
    service.save(config)
    assert service.load().harness.permission_mode == "bypass"
    assert service.load().voice.provider == "google"
    assert service.load().voice.model == "gemini-2.5-flash"
    assert service.load().voice.api_version == "2025-04-01-preview"


def test_runtime_config_service_migrates_legacy_harness_voice_fields(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "harness:\n  permission_mode: default\n  voice_enabled: true\n  voice_provider: google\n",
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


def test_runtime_config_service_defaults_reasoning_effort_to_medium(tmp_path):
    service = RuntimeConfigService(tmp_path / "missing.yaml")

    assert service.load().model.reasoning_effort == "medium"


def test_runtime_config_defaults_auto_dream_write_mode_to_review(tmp_path):
    service = RuntimeConfigService(tmp_path / "missing.yaml")

    assert service.load().harness.auto_dream_write_mode == "review"


@pytest.mark.parametrize(
    ("yaml_value", "expected"),
    [("off", "off"), ("review", "review"), ("automatic", "automatic")],
)
def test_runtime_config_accepts_auto_dream_write_modes(tmp_path, yaml_value, expected):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f'harness:\n  auto_dream_write_mode: "{yaml_value}"\n', encoding="utf-8")

    assert RuntimeConfigService(config_path).load().harness.auto_dream_write_mode == expected


@pytest.mark.parametrize(("legacy_value", "expected"), [("true", "review"), ("false", "off")])
def test_runtime_config_migrates_legacy_auto_dream_enabled(tmp_path, legacy_value, expected):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"harness:\n  auto_dream_enabled: {legacy_value}\n", encoding="utf-8")

    assert RuntimeConfigService(config_path).load().harness.auto_dream_write_mode == expected


def test_legacy_auto_dream_enabled_true_migrates_to_review(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("harness:\n  auto_dream_enabled: true\n", encoding="utf-8")

    assert RuntimeConfigService(config_path).load().harness.auto_dream_write_mode == "review"


@pytest.mark.parametrize("unsafe_value", ["true", "yes", "on", "enabled"])
def test_new_auto_dream_write_mode_rejects_automatic_aliases(tmp_path, unsafe_value):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"harness:\n  auto_dream_write_mode: {unsafe_value}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        RuntimeConfigService(config_path).load()


def test_runtime_config_accepts_root_auto_dream_write_mode(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("auto_dream_write_mode: automatic\n", encoding="utf-8")

    assert RuntimeConfigService(config_path).load().harness.auto_dream_write_mode == "automatic"


def test_runtime_config_preserves_harness_model_instance():
    config = RuntimeConfig(harness=HarnessRuntimeConfig(permission_mode="strict"))

    assert config.harness.permission_mode == "strict"


def test_runtime_config_service_normalizes_null_reasoning_effort_to_medium(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("model:\n  reasoning_effort: null\n", encoding="utf-8")

    service = RuntimeConfigService(config_path)

    assert service.load().model.reasoning_effort == "medium"


@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_runtime_config_accepts_extended_reasoning_effort_levels(effort):
    config = RuntimeConfig(model={"reasoning_effort": effort})

    assert config.model.reasoning_effort == effort


def test_runtime_config_defaults_task_delegate_batch_size_to_six():
    harness = RuntimeConfig().harness

    assert harness.task_delegate_max_batch_size == 6
    assert harness.task_delegate_max_concurrency == 4


def test_runtime_config_service_defers_task_delegate_relation_until_env_precedence(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "harness:\n  task_delegate_max_batch_size: 3\n",
        encoding="utf-8",
    )

    config = RuntimeConfigService(config_path).load()

    assert config.harness.task_delegate_max_batch_size == 3
    assert config.harness.task_delegate_max_concurrency == 4


@pytest.mark.parametrize("value", [0, 33])
def test_runtime_config_rejects_invalid_task_delegate_batch_size(value):
    with pytest.raises(ValueError):
        RuntimeConfig(harness={"task_delegate_max_batch_size": value})


@pytest.mark.parametrize("value", [0, 33])
def test_runtime_config_rejects_invalid_task_delegate_concurrency(value):
    with pytest.raises(ValueError):
        RuntimeConfig(harness={"task_delegate_max_concurrency": value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_delegate_max_batch_size", 3.0),
        ("task_delegate_max_batch_size", "3.0"),
        ("task_delegate_max_concurrency", 2.0),
        ("task_delegate_max_concurrency", "2.0"),
    ],
)
def test_runtime_config_rejects_decimal_task_delegate_limits(field, value):
    with pytest.raises(ValueError, match="expected an integer between 1 and 32"):
        RuntimeConfig(harness={field: value})


def test_runtime_config_rejects_task_delegate_concurrency_above_batch_size():
    with pytest.raises(ValueError, match="less than or equal"):
        RuntimeConfig(
            harness={
                "task_delegate_max_batch_size": 3,
                "task_delegate_max_concurrency": 4,
            }
        )
