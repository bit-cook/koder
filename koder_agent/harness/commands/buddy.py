"""Slash-command entrypoint for /buddy."""

from __future__ import annotations

from koder_agent.harness.buddy import (
    buddy_runtime,
    get_companion,
    hatch_companion,
    reaction_for_name,
    render_profile,
    to_stored_companion,
)
from koder_agent.harness.config.service import RuntimeConfigService


async def run_buddy(
    *,
    config_service: RuntimeConfigService | None = None,
    action: str | None = None,
) -> str:
    """Handle local companion lifecycle without routing through the model."""
    service = config_service or RuntimeConfigService()
    config = service.load()
    normalized_action = (action or "").strip().lower()

    if normalized_action == "status":
        companion = get_companion(config)
        if companion is None:
            return "buddy: no companion hatched yet."
        prefix = "buddy: muted" if config.harness.companion_muted else "buddy: ready"
        return render_profile(prefix, companion)

    if normalized_action == "mute":
        if config.harness.companion is None:
            return "buddy: no companion hatched yet."
        config.harness.companion_muted = True
        service.save(config)
        return "buddy: muted"

    if normalized_action == "unmute":
        if config.harness.companion is None:
            return "buddy: no companion hatched yet."
        config.harness.companion_muted = False
        service.save(config)
        return "buddy: unmuted"

    if config.harness.companion is None:
        companion = hatch_companion()
        config.harness.companion = to_stored_companion(companion)
        config.harness.companion_muted = False
        service.save(config)
        buddy_runtime.mark_hatched()
        return render_profile("buddy: hatched", companion)

    companion = get_companion(config)
    if companion is None:
        return "buddy: no companion hatched yet."
    reaction = f"{companion.name} {reaction_for_name(companion.name)}"
    buddy_runtime.mark_pet(reaction)
    return f"buddy: pet\nname: {companion.name}\nreaction: {reaction}"
