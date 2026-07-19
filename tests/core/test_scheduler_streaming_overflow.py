"""Regression: CONTEXT_OVERFLOW retry must fire on the DEFAULT streaming path.

The single compact+retry guard in ``_run_turn_unlocked`` used to be dead code on
the default interactive path: streaming is ON by default, and ``_handle_streaming``
swallowed a mid-stream overflow into ``execution_error`` and RETURNED a normal
error string instead of raising — so the ``except Exception`` retry block never
saw the overflow and ``_run_auto_compact`` was never called.

The fix makes ``_handle_streaming`` re-raise a context-overflow error (after the
Rich Live context has closed cleanly) so the caller's guard fires. These tests
drive the real streaming method via a fake ``Runner.run_streamed`` and a fake
streaming UI (which avoids Rich Live), asserting:

* an overflow mid-stream propagates out of ``_handle_streaming`` (re-raised),
* a NON-overflow error is still swallowed into a returned error string,
* end-to-end, a streaming turn that overflows once compacts and re-runs once.
"""

import io
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from agents import RawResponsesStreamEvent
from openai.types.responses.response_text_delta_event import ResponseTextDeltaEvent
from rich.console import Console
from rich.text import Text

from koder_agent.harness.memory.auto_compact import AutoCompactManager


class _ContextOverflowError(Exception):
    """Exception whose message classifies as CONTEXT_OVERFLOW."""

    def __init__(self):
        super().__init__("This model's maximum context length is 200000 tokens")


class _GenericError(Exception):
    """A non-overflow error that must NOT be retried."""


class _FakeStreamResult:
    """Stand-in for the object Runner.run_streamed returns.

    Its ``stream_events`` is an async generator that raises ``exc`` immediately,
    simulating a model error surfaced mid-stream.
    """

    def __init__(self, exc: Exception):
        self._exc = exc

    def stream_events(self):
        exc = self._exc

        async def _gen():
            raise exc
            yield  # pragma: no cover - unreachable, makes this an async generator

        return _gen()


class _PartialThenErrorStreamResult:
    """Stream one visible text delta before surfacing a provider error."""

    final_output = None

    async def stream_events(self):
        yield RawResponsesStreamEvent(
            data=ResponseTextDeltaEvent(
                content_index=0,
                delta="partial output before failure",
                item_id="msg_1",
                logprobs=[],
                output_index=0,
                sequence_number=1,
                type="response.output_text.delta",
            )
        )
        raise _GenericError("boom")


class _FakeStreamingUI:
    """Minimal StreamingOutputUI so _handle_streaming avoids Rich Live."""

    def __init__(self):
        self.final_text = None
        self.final_content = None

    def update_output(self, _renderable) -> None:
        pass

    def set_final_content(self, renderable) -> None:
        self.final_content = renderable

    def set_final_text(self, text: str) -> None:
        self.final_text = text

    def set_cancel_callback(self, _cb) -> None:
        pass


@contextmanager
def _patched_scheduler_env():
    with (
        patch("koder_agent.core.scheduler.get_all_tools", return_value=[]),
        patch("koder_agent.core.scheduler.get_display_hooks"),
        patch("koder_agent.core.scheduler.ApprovalHooks"),
        patch("koder_agent.core.scheduler.EnhancedSQLiteSession") as mock_session_cls,
        patch("koder_agent.core.scheduler.get_companion", return_value=None),
    ):
        mock_session = AsyncMock()
        mock_session.session_id = "test"
        mock_session.db_path = ":memory:"
        mock_session.get_items = AsyncMock(return_value=[])
        mock_session.clear_session = AsyncMock()
        mock_session.add_items = AsyncMock()
        mock_session.summarization_threshold = None
        mock_session_cls.return_value = mock_session
        yield mock_session


def _make_streaming_scheduler():
    from koder_agent.core.scheduler import AgentScheduler

    scheduler = AgentScheduler(session_id="test", streaming=True)
    scheduler.dev_agent = object()
    scheduler._agent_initialized = True
    scheduler._migration_done = True
    scheduler._auto_compact = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    scheduler._capture_usage = AsyncMock()
    scheduler._refresh_magic_docs_after_turn = AsyncMock()
    scheduler._repair_unreplayable_session_items = AsyncMock()
    scheduler._reconnect_unhealthy_mcp_servers = AsyncMock()
    scheduler._run_auto_compact = AsyncMock()
    scheduler._finish_goal_turn = AsyncMock()
    return scheduler


class TestHandleStreamingReRaisesOverflow:
    @pytest.mark.asyncio
    async def test_overflow_reraised_not_returned(self):
        """A mid-stream overflow must propagate out of _handle_streaming."""
        with _patched_scheduler_env():
            scheduler = _make_streaming_scheduler()
            ui = _FakeStreamingUI()
            with patch(
                "koder_agent.core.scheduler.Runner.run_streamed",
                return_value=_FakeStreamResult(_ContextOverflowError()),
            ):
                with pytest.raises(_ContextOverflowError):
                    await scheduler._handle_streaming("hi", streaming_ui=ui)

    @pytest.mark.asyncio
    async def test_non_overflow_error_still_returned_as_string(self):
        """Non-regression: a generic error is still swallowed into a string."""
        with _patched_scheduler_env():
            scheduler = _make_streaming_scheduler()
            ui = _FakeStreamingUI()
            with patch(
                "koder_agent.core.scheduler.Runner.run_streamed",
                return_value=_FakeStreamResult(_GenericError("boom")),
            ):
                result = await scheduler._handle_streaming("hi", streaming_ui=ui)
        assert "Execution error" in result
        assert scheduler._last_turn_errored is True

    @pytest.mark.asyncio
    async def test_non_overflow_error_preserves_partial_output_in_final_content(self):
        """The fixed-bottom TUI must commit streamed output before the red error."""
        with _patched_scheduler_env():
            scheduler = _make_streaming_scheduler()
            ui = _FakeStreamingUI()
            with patch(
                "koder_agent.core.scheduler.Runner.run_streamed",
                return_value=_PartialThenErrorStreamResult(),
            ):
                result = await scheduler._handle_streaming("hi", streaming_ui=ui)

        assert "Execution error: boom" in result
        assert ui.final_content is not None

        rendered = io.StringIO()
        Console(file=rendered, force_terminal=False, width=120).print(ui.final_content)
        assert "partial output before failure" in rendered.getvalue()
        assert "Execution error: boom" in rendered.getvalue()
        assert "Please provide new instructions." in rendered.getvalue()

        error_lines = [
            renderable
            for renderable in ui.final_content.renderables
            if isinstance(renderable, Text) and "Execution error: boom" in renderable.plain
        ]
        assert len(error_lines) == 1
        assert error_lines[0].style == "red"


class TestStreamingOverflowEndToEnd:
    @pytest.mark.asyncio
    async def test_streaming_turn_overflow_compacts_and_retries(self):
        """Full handle() on the streaming path: overflow -> compact -> re-run."""
        with _patched_scheduler_env():
            scheduler = _make_streaming_scheduler()

            attempts = {"n": 0}

            async def fake_handle_streaming(user_input, *, streaming_ui=None, run_input=None):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    # Simulate the real post-fix behavior: overflow re-raised.
                    raise _ContextOverflowError()
                return "recovered output"

            scheduler._handle_streaming = fake_handle_streaming
            response = await scheduler.handle("do the thing", render_output=False)
            await scheduler.cleanup()

        assert attempts["n"] == 2  # original + one retry
        scheduler._run_auto_compact.assert_called_once()
        assert "recovered output" in response

    @pytest.mark.asyncio
    async def test_streaming_second_overflow_gives_up(self):
        """A second overflow on the retry falls through to terminal error."""
        with _patched_scheduler_env():
            scheduler = _make_streaming_scheduler()

            attempts = {"n": 0}

            async def always_overflow(user_input, *, streaming_ui=None, run_input=None):
                attempts["n"] += 1
                raise _ContextOverflowError()

            scheduler._handle_streaming = always_overflow
            response = await scheduler.handle("do the thing", render_output=False)
            await scheduler.cleanup()

        assert attempts["n"] == 2  # original + exactly one retry
        scheduler._run_auto_compact.assert_called_once()
        assert "Execution error" in response
