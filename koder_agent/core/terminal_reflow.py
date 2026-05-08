"""Resize-aware replay for interactive Rich output."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Deque

from rich.console import Console

from ..utils.terminal_theme import get_adaptive_console

CLEAR_VIEWPORT = "\033[2J\033[H"
RESIZE_REFLOW_DELAY_SECONDS = 0.06


class TerminalReflowBuffer:
    """Keep recent Rich renderables so they can be redrawn after resize."""

    def __init__(
        self,
        console: Console | None = None,
        *,
        max_entries: int = 30,
        enabled: bool | None = None,
    ) -> None:
        self.console = console or get_adaptive_console()
        self.max_entries = max(1, max_entries)
        self._enabled_override = enabled
        self._entries: Deque[Any] = deque(maxlen=self.max_entries)

    def enabled(self) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        file_is_tty = getattr(self.console.file, "isatty", lambda: False)()
        return bool(self.console.is_terminal and file_is_tty)

    def record(self, renderable: Any) -> None:
        """Remember a completed interactive output renderable."""
        if not self.enabled() or renderable is None:
            return
        self._entries.append(renderable)

    def clear(self) -> None:
        self._entries.clear()

    def can_replay(self) -> bool:
        return self.enabled() and bool(self._entries)

    def replay(self) -> bool:
        """Clear the visible viewport and re-render entries at the current width."""
        if not self.can_replay():
            return False

        self.console.file.write(CLEAR_VIEWPORT)
        self.console.file.flush()
        for renderable in self._entries:
            self.console.print(renderable)
        return True

    @property
    def entries(self) -> tuple[Any, ...]:
        return tuple(self._entries)


_DEFAULT_REFLOW_BUFFER = TerminalReflowBuffer()


def get_reflow_buffer() -> TerminalReflowBuffer:
    return _DEFAULT_REFLOW_BUFFER


def record_reflow_renderable(renderable: Any) -> None:
    get_reflow_buffer().record(renderable)


def print_reflowable(console: Console, renderable: Any, **kwargs: Any) -> None:
    """Print a Rich renderable and keep it available for resize replay."""
    console.print(renderable, **kwargs)
    record_reflow_renderable(renderable)


async def _replay_after_resize(buffer: TerminalReflowBuffer) -> None:
    await asyncio.sleep(RESIZE_REFLOW_DELAY_SECONDS)
    if not buffer.can_replay():
        return

    from prompt_toolkit.application import run_in_terminal

    await run_in_terminal(buffer.replay, render_cli_done=False)


def attach_prompt_resize_reflow(
    app: Any,
    *,
    buffer: TerminalReflowBuffer | None = None,
) -> None:
    """Wrap a prompt_toolkit Application resize callback with output replay."""
    reflow_buffer = buffer or get_reflow_buffer()
    original_on_resize = app._on_resize

    def _on_resize_with_reflow() -> None:
        original_on_resize()
        if not reflow_buffer.can_replay():
            return

        existing_task = getattr(app, "_koder_resize_reflow_task", None)
        if existing_task is not None and not existing_task.done():
            existing_task.cancel()

        task = app.create_background_task(_replay_after_resize(reflow_buffer))
        setattr(app, "_koder_resize_reflow_task", task)

    app._on_resize = _on_resize_with_reflow
