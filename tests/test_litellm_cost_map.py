from __future__ import annotations

import importlib
import os
import types

import koder_agent
from koder_agent.litellm_cost_map import (
    LITELLM_LOCAL_MODEL_COST_MAP_ENV,
    configure_litellm_local_model_cost_map,
    get_litellm_cost_map_debug_lines,
    install_vendored_litellm_model_cost_map,
    load_vendored_model_cost_map,
)


def test_package_init_forces_litellm_local_cost_map(monkeypatch):
    monkeypatch.setenv(LITELLM_LOCAL_MODEL_COST_MAP_ENV, "false")

    importlib.reload(koder_agent)

    assert koder_agent.__version__
    assert os.environ[LITELLM_LOCAL_MODEL_COST_MAP_ENV] == "true"


def test_configure_litellm_local_model_cost_map(monkeypatch):
    monkeypatch.delenv(LITELLM_LOCAL_MODEL_COST_MAP_ENV, raising=False)

    configure_litellm_local_model_cost_map()

    assert os.environ[LITELLM_LOCAL_MODEL_COST_MAP_ENV] == "true"


def test_load_vendored_model_cost_map_has_known_model_metadata():
    model_cost_map = load_vendored_model_cost_map()

    assert len(model_cost_map) > 1000
    assert "gpt-4o" in model_cost_map
    assert "max_input_tokens" in model_cost_map["gpt-4o"]


def test_install_vendored_litellm_model_cost_map_merges_custom_entries():
    fake_litellm = types.SimpleNamespace(
        model_cost={
            "custom-model": {"max_input_tokens": 123},
            "gpt-4o": {"max_input_tokens": 1},
        }
    )

    installed = install_vendored_litellm_model_cost_map(fake_litellm)

    vendored = load_vendored_model_cost_map()
    assert installed["custom-model"]["max_input_tokens"] == 123
    assert installed["gpt-4o"]["max_input_tokens"] == vendored["gpt-4o"]["max_input_tokens"]
    assert "gpt-4o" in fake_litellm.model_cost


def test_install_vendored_litellm_model_cost_map_is_idempotent():
    fake_litellm = types.SimpleNamespace(model_cost={})

    installed = install_vendored_litellm_model_cost_map(fake_litellm)
    reinstalled = install_vendored_litellm_model_cost_map(fake_litellm)

    assert reinstalled is installed


def test_install_vendored_litellm_model_cost_map_handles_litellm_reinit():
    fake_litellm = types.SimpleNamespace(model_cost={})
    install_vendored_litellm_model_cost_map(fake_litellm)

    fake_litellm.model_cost = {"custom-model": {"input_cost_per_token": 0.1}}
    reinstalled = install_vendored_litellm_model_cost_map(fake_litellm)

    assert reinstalled["custom-model"]["input_cost_per_token"] == 0.1
    assert "gpt-4o" in reinstalled


def test_koder_litellm_entrypoint_installs_vendored_model_cost_map():
    import litellm

    import koder_agent.utils.client  # noqa: F401

    vendored = load_vendored_model_cost_map()
    assert len(litellm.model_cost) >= len(vendored)
    assert (
        litellm.model_cost["gpt-4o"]["max_input_tokens"] == vendored["gpt-4o"]["max_input_tokens"]
    )


def test_litellm_cost_map_debug_lines_include_init_process(monkeypatch):
    monkeypatch.setenv(LITELLM_LOCAL_MODEL_COST_MAP_ENV, "true")
    fake_litellm = types.SimpleNamespace(model_cost={"gpt-4o": {"max_input_tokens": 128000}})

    lines = get_litellm_cost_map_debug_lines(fake_litellm)

    rendered = "\n".join(lines)
    assert "LiteLLM cost data init:" in rendered
    assert "local_mode_env: true" in rendered
    assert "vendored_entries:" in rendered
    assert "active_entries: 1" in rendered
    assert "events:" in rendered


def test_litellm_cost_map_debug_lines_include_source_info_errors(monkeypatch):
    fake_litellm = types.SimpleNamespace(model_cost={})

    def fake_source_info():
        return {"error": "missing LiteLLM source info"}

    monkeypatch.setattr(
        "koder_agent.litellm_cost_map._get_litellm_model_cost_map_source_info",
        fake_source_info,
    )

    rendered = "\n".join(get_litellm_cost_map_debug_lines(fake_litellm))

    assert "source_info_error: missing LiteLLM source info" in rendered
