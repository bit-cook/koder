from __future__ import annotations

import asyncio
import time

from rich.console import Console
from rich.text import Text

from koder_agent.harness.buddy import (
    BuddyDisplayState,
    BuddyLiveLayout,
    buddy_runtime,
    get_companion,
    observe_turn,
    render_companion_lines,
)
from koder_agent.harness.commands.buddy import run_buddy
from koder_agent.harness.config.schema import (
    HarnessCompanionConfig,
    HarnessRuntimeConfig,
    RuntimeConfig,
)
from koder_agent.harness.config.service import RuntimeConfigService


def _config(*, name: str = "Pip") -> RuntimeConfig:
    return RuntimeConfig(
        harness=HarnessRuntimeConfig(
            companion=HarnessCompanionConfig(
                name=name,
                personality="quietly forensic",
                hatched_at=1,
            ),
            companion_muted=False,
        )
    )


def test_render_companion_lines_show_sprite_and_name(monkeypatch):
    monkeypatch.setenv("USER", "buddy-render")
    companion = get_companion(_config())

    assert companion is not None

    lines = render_companion_lines(
        companion,
        state=BuddyDisplayState(),
        now=0.0,
        columns=120,
    )

    assert any("Pip" in line for line in lines)
    assert len(lines) >= 5
    sprite_lines = [line for line in lines if line.strip() and "Pip" not in line]
    assert any(any(char in line for char in "/\\()[]<>._~") for line in sprite_lines)


def test_render_companion_lines_show_pet_burst_and_busy_frames(monkeypatch):
    monkeypatch.setenv("USER", "buddy-render")
    companion = get_companion(_config())

    assert companion is not None

    pet_lines = render_companion_lines(
        companion,
        state=BuddyDisplayState(pet_at=0.0),
        now=0.0,
        columns=120,
    )
    busy_a = render_companion_lines(
        companion,
        state=BuddyDisplayState(busy=True),
        now=0.0,
        columns=120,
    )
    busy_b = render_companion_lines(
        companion,
        state=BuddyDisplayState(busy=True),
        now=0.6,
        columns=120,
    )

    assert any("♥" in line for line in pet_lines)
    assert busy_a != busy_b


def test_render_companion_lines_show_reaction_bubble_when_requested(monkeypatch):
    monkeypatch.setenv("USER", "buddy-render")
    companion = get_companion(_config())

    assert companion is not None

    lines = render_companion_lines(
        companion,
        state=BuddyDisplayState(reaction="keeps one eye on the loose ends."),
        now=0.0,
        columns=120,
        show_reaction=True,
    )

    assert any("loose ends" in line for line in lines)
    assert any(line.startswith(".") or line.startswith("|") for line in lines)


def test_observe_turn_reacts_to_direct_address(monkeypatch):
    monkeypatch.setenv("USER", "buddy-observer")
    companion = get_companion(_config(name="Tango"))

    assert companion is not None

    reaction = observe_turn(
        companion=companion,
        user_input="Tango, does this test smell right?",
        assistant_output="I think the patch is reasonable.",
    )

    assert reaction is not None
    assert "Tango" in reaction


def test_observe_turn_reacts_to_passing_tests(monkeypatch):
    monkeypatch.setenv("USER", "buddy-observer")
    companion = get_companion(_config())

    assert companion is not None

    reaction = observe_turn(
        companion=companion,
        user_input="Run the focused test suite.",
        assistant_output="All focused tests passed and the fix looks good.",
    )

    assert reaction == "does a tiny victory lap."


def test_run_buddy_updates_runtime_surface_state(tmp_path, monkeypatch):
    monkeypatch.setenv("USER", "buddy-surface")
    config_path = tmp_path / ".koder" / "config.yaml"
    service = RuntimeConfigService(config_path=config_path)
    buddy_runtime.reset()

    asyncio.run(run_buddy(config_service=service))
    asyncio.run(run_buddy(config_service=service))

    snapshot = buddy_runtime.snapshot(now=time.time())

    assert snapshot.pet_at is not None
    assert snapshot.reaction is not None


def test_buddy_live_layout_renders_companion_with_body(monkeypatch):
    monkeypatch.setenv("USER", "buddy-live-layout")
    buddy_runtime.reset()
    buddy_runtime.mark_task_start()
    console = Console(width=120, record=True)
    layout = BuddyLiveLayout(
        body_getter=lambda: Text("thinking..."),
        config_getter=lambda: _config(name="Pip"),
    )

    console.print(layout)
    rendered = console.export_text()

    assert "thinking..." in rendered
    assert "Pip" in rendered
