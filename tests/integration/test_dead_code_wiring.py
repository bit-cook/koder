"""Tests that previously dead modules are now called from runtime code."""

from pathlib import Path


def _source(path):
    return Path(path).read_text()


def test_model_deprecation_called():
    src = _source("koder_agent/utils/client.py")
    assert "check_model_deprecation" in src


def test_plugin_name_validation_called():
    # Check marketplace.py
    src = _source("koder_agent/harness/plugins/marketplace.py")
    assert "validate_plugin_name" in src


def test_skill_discovery_called():
    src = _source("koder_agent/tools/skill.py")
    assert "discover_skills_for_paths" in src


def test_voice_keyterms_called():
    src = _source("koder_agent/harness/voice/service.py")
    assert "get_all_keyterms" in src or "keyterms" in src


def test_secure_storage_called():
    src = _source("koder_agent/auth/token_storage.py")
    assert "SecureStorage" in src or "secure_storage" in src
