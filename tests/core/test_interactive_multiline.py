"""Render-level tests for multiline interactive prompt input."""

import asyncio
from dataclasses import dataclass

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.widgets import Frame

from koder_agent.core import interactive


class _SizedDummyOutput(DummyOutput):
    """Dummy output with a deterministic terminal size."""

    def __init__(self, *, rows: int = 60, columns: int = 40) -> None:
        self._size = Size(rows=rows, columns=columns)

    def get_size(self) -> Size:
        return self._size


@dataclass(frozen=True)
class _RenderSnapshot:
    window_height: int
    cursor_y: int
    displayed_lines: tuple[int, ...]
    vertical_scroll: int
    buffer_text: str


async def _render_input(text: str, *, columns: int = 40) -> _RenderSnapshot:
    buffer = Buffer(document=Document(text=text, cursor_position=len(text)))
    window = interactive._create_input_window(BufferControl(buffer=buffer))

    with create_pipe_input() as pipe_input:
        with create_app_session(
            input=pipe_input,
            output=_SizedDummyOutput(columns=columns),
        ):
            app = Application(layout=Layout(HSplit([Frame(window)])))
            task = asyncio.create_task(app.run_async())
            try:
                for _ in range(50):
                    render_info = window.render_info
                    if render_info is not None:
                        break
                    await asyncio.sleep(0.01)
                else:
                    pytest.fail("input window did not render")

                return _RenderSnapshot(
                    window_height=render_info.window_height,
                    cursor_y=render_info.cursor_position.y,
                    displayed_lines=tuple(render_info.displayed_lines),
                    vertical_scroll=render_info.vertical_scroll,
                    buffer_text=buffer.text,
                )
            finally:
                app.exit(result=None)
                await asyncio.wait_for(task, timeout=2)


def test_input_window_wraps_and_caps_visible_rows() -> None:
    assert interactive.MAX_INPUT_VISIBLE_LINES == 30

    buffer_control = BufferControl(buffer=Buffer())
    window = interactive._create_input_window(buffer_control)

    assert window.content is buffer_control
    assert window.height.min == 1
    assert window.height.max == interactive.MAX_INPUT_VISIBLE_LINES
    assert window.wrap_lines()
    assert window.dont_extend_height()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("logical_line_count", "expected_height", "expected_vertical_scroll"),
    [
        (29, 29, 0),
        (30, 30, 0),
        (31, 30, 1),
    ],
)
async def test_explicit_lines_cap_at_thirty_visible_rows(
    logical_line_count: int,
    expected_height: int,
    expected_vertical_scroll: int,
) -> None:
    text = "\n".join(f"line {index}" for index in range(logical_line_count))

    snapshot = await _render_input(text)

    assert snapshot.window_height == expected_height
    assert len(snapshot.displayed_lines) == expected_height
    assert snapshot.displayed_lines[-1] == logical_line_count - 1
    assert snapshot.cursor_y == expected_height - 1
    assert snapshot.vertical_scroll == expected_vertical_scroll


@pytest.mark.asyncio
async def test_long_logical_line_soft_wraps_without_changing_buffer_text() -> None:
    text = "0123456789" * 12

    snapshot = await _render_input(text, columns=24)

    assert snapshot.window_height > 1
    assert snapshot.cursor_y == snapshot.window_height - 1
    assert len(snapshot.displayed_lines) == snapshot.window_height
    assert set(snapshot.displayed_lines) == {0}
    assert snapshot.buffer_text == text
    assert "\n" not in snapshot.buffer_text
