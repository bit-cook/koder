"""State-driven shell for the harness runtime UI."""

from __future__ import annotations

from dataclasses import dataclass

from koder_agent.harness.state.actions import AppendNotification
from koder_agent.harness.state.store import HarnessStore
from koder_agent.harness.ui.screens.main import render_main_screen


@dataclass(frozen=True)
class InputResult:
    dispatched_action: object | None


class HarnessApp:
    def __init__(self, store: HarnessStore):
        self.store = store

    def render_frame(self) -> dict:
        return render_main_screen(mode=self.store.state.mode)

    def handle_key(self, key: str) -> InputResult:
        if key == "tab":
            action = AppendNotification(message="tab")
            self.store.dispatch(action)
            return InputResult(dispatched_action=action)
        return InputResult(dispatched_action=None)
