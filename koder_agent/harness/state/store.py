"""Copy-on-write state store for the harness runtime."""

from __future__ import annotations

from dataclasses import replace

from .actions import AppendNotification, SetMode
from .models import HarnessState, Notification


class HarnessStore:
    def __init__(self, state: HarnessState):
        self.state = state

    @classmethod
    def initial(cls) -> "HarnessStore":
        return cls(HarnessState())

    def dispatch(self, action) -> None:
        if isinstance(action, SetMode):
            self.state = replace(self.state, mode=action.mode)
            return
        if isinstance(action, AppendNotification):
            notifications = [*self.state.notifications, Notification(message=action.message)]
            self.state = replace(self.state, notifications=notifications)
            return
        raise ValueError(f"Unsupported action: {type(action).__name__}")
