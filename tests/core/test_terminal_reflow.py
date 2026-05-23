from io import StringIO

from rich.console import Console

from koder_agent.core.terminal_reflow import (
    CLEAR_VIEWPORT,
    TerminalReflowBuffer,
    attach_prompt_resize_reflow,
)


class WidthProbe:
    def __init__(self) -> None:
        self.widths: list[int] = []

    def __rich_console__(self, _console, options):
        self.widths.append(options.max_width)
        yield f"width={options.max_width}"


def test_reflow_replays_renderables_at_current_console_width():
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=80, color_system=None)
    buffer = TerminalReflowBuffer(console, enabled=True)
    probe = WidthProbe()

    buffer.record(probe)
    console._width = 42
    console._height = 25

    assert buffer.replay() is True

    rendered = output.getvalue()
    assert rendered.startswith(CLEAR_VIEWPORT)
    assert "\033[3J" not in rendered
    assert probe.widths == [42]
    assert "width=42" in rendered


def test_reflow_buffer_keeps_recent_entries_only():
    console = Console(file=StringIO(), force_terminal=True, color_system=None)
    buffer = TerminalReflowBuffer(console, max_entries=2, enabled=True)

    buffer.record("one")
    buffer.record("two")
    buffer.record("three")

    assert buffer.entries == ("two", "three")


def test_attach_prompt_resize_reflow_wraps_original_resize_callback():
    console = Console(file=StringIO(), force_terminal=True, color_system=None)
    buffer = TerminalReflowBuffer(console, enabled=True)
    buffer.record("transcript")
    calls: list[str] = []
    scheduled = []

    class Task:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            calls.append("cancel")

    class App:
        def _on_resize(self) -> None:
            calls.append("resize")

        def create_background_task(self, coroutine):
            scheduled.append(coroutine)
            coroutine.close()
            return Task()

    app = App()
    attach_prompt_resize_reflow(app, buffer=buffer)

    app._on_resize()

    assert calls == ["resize"]
    assert len(scheduled) == 1
    assert hasattr(app, "_koder_resize_reflow_task")
