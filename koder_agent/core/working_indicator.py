"""Claude-style animated working indicator shared by both streaming UIs.

While a turn is running, interactive mode renders the indicator in a dedicated
prompt_toolkit window above the input box, and non-interactive mode appends it
as the last line of the Rich Live region. Both renderers poll on their own
refresh timers, so the animation is derived statelessly from elapsed time.
"""

from __future__ import annotations

import random
import threading
import time

# Rich Live renders from a background thread while the scheduler mutates state
# on the event loop, so all access goes through a lock (same as BuddyRuntime).

_WORDS = (
    "Brewing",
    "Burrowing",
    "Chiseling",
    "Cogitating",
    "Composing",
    "Conjuring",
    "Distilling",
    "Doodling",
    "Foraging",
    "Forging",
    "Grokking",
    "Hatching",
    "Incubating",
    "Kneading",
    "Levitating",
    "Marinating",
    "Meandering",
    "Moseying",
    "Mulling",
    "Musing",
    "Noodling",
    "Orbiting",
    "Percolating",
    "Pondering",
    "Puttering",
    "Riffing",
    "Ruminating",
    "Scheming",
    "Simmering",
    "Sleuthing",
    "Slithering",
    "Snorkeling",
    "Spelunking",
    "Tinkering",
    "Untangling",
    "Whirring",
    "Whittling",
    "Wrangling",
)


class WorkingIndicator:
    """Thread-safe turn-progress indicator with a stateless spinner animation."""

    FRAMES = ("·", "✢", "✳", "✶", "✻", "✽")
    FRAME_SECONDS = 0.25

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._word: str = ""
        self._activity: str | None = None

    def begin(self, *, now: float | None = None) -> None:
        """Start (or restart) the indicator with a fresh word for this turn."""
        with self._lock:
            self._started_at = time.monotonic() if now is None else now
            self._word = random.choice(_WORDS)
            self._activity = None

    def finish(self) -> None:
        with self._lock:
            self._started_at = None
            self._word = ""
            self._activity = None

    def set_activity(self, activity: str | None) -> None:
        """Set (or clear) the currently-running tool hint."""
        with self._lock:
            self._activity = activity

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._started_at is not None

    def status_parts(self, *, now: float | None = None, esc_hint: bool = True) -> tuple[str, str]:
        """Return ("✳ Cogitating…", "(12s · run_shell · esc to interrupt)")."""
        with self._lock:
            if self._started_at is None:
                return "", ""
            elapsed = max(0.0, (time.monotonic() if now is None else now) - self._started_at)
            word = self._word
            activity = self._activity

        head = f"{self._frame(elapsed)} {word}…"
        segments = [f"{int(elapsed)}s"]
        if activity:
            segments.append(activity)
        if esc_hint:
            segments.append("esc to interrupt")
        return head, f"({' · '.join(segments)})"

    def status_text(self, *, now: float | None = None, esc_hint: bool = True) -> str:
        head, detail = self.status_parts(now=now, esc_hint=esc_hint)
        if not head:
            return ""
        return f"{head} {detail}"

    @classmethod
    def _frame(cls, elapsed: float) -> str:
        # Ping-pong through FRAMES: indices 0..5 then 4..1, a 10-step cycle.
        cycle = 2 * len(cls.FRAMES) - 2
        seq = int(elapsed / cls.FRAME_SECONDS) % cycle
        index = seq if seq < len(cls.FRAMES) else cycle - seq
        return cls.FRAMES[index]


working_indicator = WorkingIndicator()
