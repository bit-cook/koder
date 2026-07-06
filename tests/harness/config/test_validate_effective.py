from __future__ import annotations

import argparse

import pytest
import yaml

from koder_agent.config.manager import ConfigManager
from koder_agent.harness.config import commands
from koder_agent.harness.config.service import RuntimeConfigService


def _patch_validate_service(monkeypatch, config_path):
    """Make _handle_config_validate use a RuntimeConfigService at config_path."""
    monkeypatch.setattr(
        commands,
        "RuntimeConfigService",
        lambda *a, **k: RuntimeConfigService(config_path=config_path),
    )


def test_config_validate_valid(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"model": {"name": "gpt-4.1", "provider": "openai"}}),
        encoding="utf-8",
    )
    _patch_validate_service(monkeypatch, config_path)
    exit_code = commands._handle_config_validate()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Config valid" in out


def test_config_validate_invalid_schema(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    # reasoning_effort accepts only a constrained literal set; a bogus value fails.
    config_path.write_text(
        yaml.safe_dump({"model": {"reasoning_effort": "turbo"}}),
        encoding="utf-8",
    )
    _patch_validate_service(monkeypatch, config_path)
    exit_code = commands._handle_config_validate()
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Config invalid" in out


def test_config_validate_bad_yaml(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("model: [unbalanced\n", encoding="utf-8")
    _patch_validate_service(monkeypatch, config_path)
    exit_code = commands._handle_config_validate()
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "Config invalid" in out


def test_config_validate_missing_file_ok(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "missing.yaml"
    _patch_validate_service(monkeypatch, config_path)
    exit_code = commands._handle_config_validate()
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "defaults are valid" in out


@pytest.mark.asyncio
async def test_config_show_effective_overlays_env(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"model": {"name": "file-model", "provider": "openai"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "get_config_manager", lambda: ConfigManager(config_path))
    monkeypatch.setenv("KODER_MODEL", "env-model")

    args = argparse.Namespace(config_action="show", effective=True)
    exit_code = await commands.handle_config_subcommand(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    dumped = yaml.safe_load(out)
    assert dumped["model"]["name"] == "env-model"


@pytest.mark.asyncio
async def test_config_show_without_effective_ignores_env(monkeypatch, tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"model": {"name": "file-model", "provider": "openai"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "get_config_manager", lambda: ConfigManager(config_path))
    monkeypatch.setenv("KODER_MODEL", "env-model")

    args = argparse.Namespace(config_action="show", effective=False)
    exit_code = await commands.handle_config_subcommand(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    dumped = yaml.safe_load(out)
    assert dumped["model"]["name"] == "file-model"
