"""Source-backed brief-mode toggle support."""

from __future__ import annotations

from koder_agent.harness.config.service import RuntimeConfigService


async def run_brief(*, config_service: RuntimeConfigService | None = None) -> str:
    """Toggle brief-only mode."""
    service = config_service or RuntimeConfigService()
    config = service.load()
    new_state = not config.harness.brief_mode_enabled
    config.harness.brief_mode_enabled = new_state
    service.save(config)
    return "Brief-only mode enabled" if new_state else "Brief-only mode disabled"
