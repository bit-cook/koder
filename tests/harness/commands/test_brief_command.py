from __future__ import annotations

import asyncio

from koder_agent.harness.commands.brief import run_brief
from koder_agent.harness.config.service import RuntimeConfigService


def test_run_brief_toggles_brief_mode_and_persists(tmp_path):
    config_path = tmp_path / ".koder" / "config.yaml"
    service = RuntimeConfigService(config_path=config_path)

    enabled = asyncio.run(run_brief(config_service=service))
    assert enabled == "Brief-only mode enabled"
    assert service.load().harness.brief_mode_enabled is True

    disabled = asyncio.run(run_brief(config_service=service))
    assert disabled == "Brief-only mode disabled"
    assert service.load().harness.brief_mode_enabled is False
