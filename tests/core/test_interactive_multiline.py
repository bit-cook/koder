"""Render-level tests for multiline interactive prompt input."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.widgets import Frame

from koder_agent.core import interactive
from koder_agent.core.queued_input import QueuedInputManager

_MISSING = object()


@pytest.fixture(autouse=True)
def _restore_prompt_toolkit_shift_enter_state():
    """Keep prompt_toolkit's process-global parser tables isolated per test."""
    from prompt_toolkit.input import ansi_escape_sequences, vt100_parser

    sequence_table = ansi_escape_sequences.ANSI_SEQUENCES
    prefix_cache = vt100_parser._IS_PREFIX_OF_LONGER_MATCH_CACHE
    originals = {
        sequence: sequence_table.get(sequence, _MISSING)
        for sequence in interactive.SHIFT_ENTER_SEQUENCES
    }
    cached_prefixes = dict(prefix_cache)
    yield
    for sequence, original in originals.items():
        if original is _MISSING:
            sequence_table.pop(sequence, None)
        else:
            sequence_table[sequence] = original
    prefix_cache.clear()
    prefix_cache.update(cached_prefixes)


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
    screen_cursor_y: int
    displayed_lines: tuple[int, ...]
    vertical_scroll: int
    buffer_text: str


async def _render_input(text: str, *, columns: int = 40) -> _RenderSnapshot:
    buffer = Buffer(document=Document(text=text, cursor_position=len(text)))
    window = interactive._create_input_window(
        BufferControl(buffer=buffer, input_processors=[BeforeInput("> ")])
    )

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

                screen = app.renderer.last_rendered_screen
                write_position = screen.visible_windows_to_write_positions[window]
                screen_cursor = screen.get_cursor_position(window)
                return _RenderSnapshot(
                    window_height=render_info.window_height,
                    cursor_y=render_info.cursor_position.y,
                    screen_cursor_y=screen_cursor.y - write_position.ypos,
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


@pytest.mark.asyncio
async def test_capped_soft_wrap_keeps_cursor_visible_at_exact_boundary() -> None:
    columns = 12
    input_width = columns - 2  # Frame borders.
    text = "x" * (input_width * interactive.MAX_INPUT_VISIBLE_LINES - len("> "))

    snapshot = await _render_input(text, columns=columns)

    assert snapshot.window_height == interactive.MAX_INPUT_VISIBLE_LINES
    assert snapshot.cursor_y == snapshot.window_height - 1
    assert snapshot.screen_cursor_y == snapshot.window_height - 1


@pytest.mark.asyncio
async def test_capped_soft_wrap_keeps_wide_character_cursor_visible() -> None:
    snapshot = await _render_input("a界" * 50, columns=6)

    assert snapshot.window_height == interactive.MAX_INPUT_VISIBLE_LINES
    assert snapshot.cursor_y == snapshot.window_height - 1
    assert snapshot.screen_cursor_y == snapshot.window_height - 1


@pytest.mark.asyncio
async def test_capped_wide_character_wrap_scrolls_past_prior_logical_line() -> None:
    snapshot = await _render_input("x\n" + "a界" * 38, columns=6)

    assert snapshot.window_height == interactive.MAX_INPUT_VISIBLE_LINES
    assert snapshot.vertical_scroll == 1
    assert snapshot.cursor_y == snapshot.window_height - 1
    assert snapshot.screen_cursor_y == snapshot.window_height - 1


def _parsed_keys(sequence: str):
    key_presses = []
    parser = Vt100Parser(key_presses.append)
    parser.feed_and_flush(sequence)
    return [key_press.key for key_press in key_presses]


def test_shift_enter_registration_clears_cached_unknown_prefix() -> None:
    from prompt_toolkit.input import ansi_escape_sequences, vt100_parser

    csi_u = "\x1b[13;2u"
    ansi_escape_sequences.ANSI_SEQUENCES.pop(csi_u, None)
    vt100_parser._IS_PREFIX_OF_LONGER_MATCH_CACHE.clear()

    assert _parsed_keys(csi_u) != [Keys.ControlJ]
    assert vt100_parser._IS_PREFIX_OF_LONGER_MATCH_CACHE

    assert interactive._register_shift_enter_sequences()
    assert not vt100_parser._IS_PREFIX_OF_LONGER_MATCH_CACHE
    assert _parsed_keys(csi_u) == [Keys.ControlJ]


@pytest.mark.parametrize("sequence", interactive.SHIFT_ENTER_SEQUENCES)
def test_shift_enter_sequences_map_to_existing_newline_chord(sequence: str) -> None:
    assert interactive._register_shift_enter_sequences()
    assert _parsed_keys(sequence) == [Keys.ControlJ]


def test_shift_enter_registration_is_idempotent() -> None:
    assert interactive._register_shift_enter_sequences()
    assert interactive._register_shift_enter_sequences()


def test_shift_enter_registration_fails_open_without_prefix_cache(monkeypatch) -> None:
    from prompt_toolkit.input import vt100_parser

    monkeypatch.delattr(vt100_parser, "_IS_PREFIX_OF_LONGER_MATCH_CACHE")
    assert not interactive._register_shift_enter_sequences()


def test_shift_enter_registration_rolls_back_when_cache_clear_fails(monkeypatch) -> None:
    from prompt_toolkit.input import ansi_escape_sequences, vt100_parser

    before = {
        sequence: ansi_escape_sequences.ANSI_SEQUENCES.get(sequence, _MISSING)
        for sequence in interactive.SHIFT_ENTER_SEQUENCES
    }

    class _FailingCache(dict):
        def clear(self) -> None:
            raise RuntimeError("cache clear failed")

    monkeypatch.setattr(
        vt100_parser,
        "_IS_PREFIX_OF_LONGER_MATCH_CACHE",
        _FailingCache(),
    )

    assert not interactive._register_shift_enter_sequences()
    for sequence, original in before.items():
        current = ansi_escape_sequences.ANSI_SEQUENCES.get(sequence, _MISSING)
        if original is _MISSING:
            assert current is _MISSING
        else:
            assert current == original


async def _submit_prompt(*chunks: str, columns: int = 80) -> str:
    prompt = interactive.InteractivePrompt(commands={})
    output = _SizedDummyOutput(columns=columns)
    with create_pipe_input() as pipe_input:
        with create_app_session(input=pipe_input, output=output):
            task = asyncio.create_task(prompt.get_input())
            try:
                await asyncio.sleep(0)
                for chunk in chunks:
                    pipe_input.send_text(chunk)
                return await asyncio.wait_for(task, timeout=2)
            finally:
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task


@pytest.mark.asyncio
async def test_shift_enter_inserts_newline_and_plain_enter_submits() -> None:
    result = await _submit_prompt(" first line", "\x1b[13;2u", "second line  \r")

    assert result == "first line\nsecond line"


@pytest.mark.asyncio
async def test_plain_enter_still_submits_without_newline() -> None:
    assert await _submit_prompt("single line\r") == "single line"


@pytest.mark.asyncio
async def test_soft_wrapped_submission_does_not_gain_newlines() -> None:
    text = "soft-wrap-submit-" + "0123456789" * 12

    result = await _submit_prompt(text + "\r", columns=24)

    assert result == text
    assert "\n" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("newline_sequence", ("\n", "\x1b\r"))
async def test_existing_newline_fallback_keys_still_work(newline_sequence: str) -> None:
    result = await _submit_prompt("first", newline_sequence, "second\r")

    assert result == "first\nsecond"


@pytest.mark.asyncio
async def test_shift_enter_preserves_newline_in_streaming_queued_input() -> None:
    prompt = interactive.InteractivePrompt(commands={})
    manager = QueuedInputManager()
    stop_event = asyncio.Event()

    with create_pipe_input() as pipe_input:
        with create_app_session(input=pipe_input, output=DummyOutput()):
            task = asyncio.create_task(
                prompt._run_input_app(queue_manager=manager, stop_event=stop_event)
            )
            try:
                await asyncio.sleep(0)
                pipe_input.send_text("queued first")
                pipe_input.send_text("\x1b[27;2;13~")
                pipe_input.send_text("queued second\r")

                for _ in range(100):
                    if manager.has_pending():
                        break
                    await asyncio.sleep(0.01)
                assert manager.has_pending()
            finally:
                stop_event.set()
                await asyncio.wait_for(task, timeout=2)

    assert manager.drain_for_tool_result() == ["queued first\nqueued second"]
