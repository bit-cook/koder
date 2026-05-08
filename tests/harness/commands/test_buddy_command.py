from __future__ import annotations

import asyncio

from koder_agent.harness.commands.buddy import run_buddy
from koder_agent.harness.config.service import RuntimeConfigService


def test_run_buddy_hatches_companion_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("USER", "buddy-tester")
    config_path = tmp_path / ".koder" / "config.yaml"
    service = RuntimeConfigService(config_path=config_path)

    output = asyncio.run(run_buddy(config_service=service))

    assert output.startswith("buddy: hatched")
    assert "name:" in output
    assert "species:" in output
    companion = service.load().harness.companion
    assert companion is not None
    assert companion.name
    assert companion.personality


def test_run_buddy_pets_existing_companion(tmp_path, monkeypatch):
    monkeypatch.setenv("USER", "buddy-tester")
    config_path = tmp_path / ".koder" / "config.yaml"
    service = RuntimeConfigService(config_path=config_path)

    first = asyncio.run(run_buddy(config_service=service))
    companion = service.load().harness.companion
    assert companion is not None

    second = asyncio.run(run_buddy(config_service=service))

    assert first.startswith("buddy: hatched")
    assert second.startswith("buddy: pet")
    assert companion.name in second
