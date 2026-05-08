"""Tests for the Config get/set tool."""

import json

import pytest

from koder_agent.config.manager import ConfigManager, reset_config_manager


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path):
    ConfigManager.DEFAULT_CONFIG_PATH = tmp_path / "config.yaml"
    reset_config_manager()
    yield
    ConfigManager.DEFAULT_CONFIG_PATH = None
    reset_config_manager()


def test_config_get_model():
    from koder_agent.tools.config_tool import config_tool

    result = json.loads(config_tool(setting="model.name"))
    assert result["success"] is True
    assert result["operation"] == "get"
    assert isinstance(result["value"], str)


def test_config_set_model_name():
    from koder_agent.tools.config_tool import config_tool

    result = json.loads(config_tool(setting="model.name", value="gpt-5"))
    assert result["success"] is True
    assert result["operation"] == "set"
    assert result["new_value"] == "gpt-5"
    # Verify persisted
    get_result = json.loads(config_tool(setting="model.name"))
    assert get_result["value"] == "gpt-5"


def test_config_get_unknown_setting():
    from koder_agent.tools.config_tool import config_tool

    result = json.loads(config_tool(setting="nonexistent.key"))
    assert result["success"] is False


def test_config_set_boolean():
    from koder_agent.tools.config_tool import config_tool

    result = json.loads(config_tool(setting="cli.stream", value="false"))
    assert result["success"] is True
    assert result["new_value"] is False
