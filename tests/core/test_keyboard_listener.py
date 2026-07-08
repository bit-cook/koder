"""Tests for keyboard listener ESC sequence handling (M9 fix)."""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.core.keyboard_listener import KeyboardListener


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-only TTY tests")
class TestEscSequenceDisambiguation:
    """Verify that escape sequences split across reads do not false-cancel."""

    @pytest.mark.asyncio
    async def test_esc_sequence_not_false_cancel(self):
        """Arrow key split across reads (ESC then [A) must NOT trigger on_escape."""
        listener = KeyboardListener()
        on_escape = AsyncMock()
        call_count = 0

        # Simulate: first _key_available returns True, _read_available returns lone ESC.
        # After the 25ms sleep, _key_available returns True (trailing bytes arrived),
        # then _read_available consumes them. On next loop iteration, no more keys.
        key_available_returns = iter([True, True, False])
        read_available_returns = iter([chr(27), "[A"])

        def fake_key_available():
            try:
                return next(key_available_returns)
            except StopIteration:
                return False

        def fake_read_available():
            try:
                return next(read_available_returns)
            except StopIteration:
                return ""

        with (
            patch.object(listener, "_is_unix_tty", return_value=True),
            patch.object(listener, "_setup_terminal"),
            patch.object(listener, "_restore_terminal"),
            patch.object(listener, "_key_available", side_effect=fake_key_available),
            patch.object(listener, "_read_available", side_effect=fake_read_available),
        ):

            async def patched_sleep(duration):
                nonlocal call_count
                call_count += 1
                if call_count > 3:
                    listener.stop()

            with patch.object(asyncio, "sleep", side_effect=patched_sleep):
                await listener.listen(on_escape=on_escape, poll_interval=0.01)

        # The escape callback must NOT have been called.
        on_escape.assert_not_called()

    @pytest.mark.asyncio
    async def test_standalone_esc_still_triggers(self):
        """A genuine standalone ESC (no follow-up bytes) must still trigger on_escape."""
        listener = KeyboardListener()
        on_escape = AsyncMock()

        # Simulate: _key_available returns True, _read_available returns lone ESC.
        # After the 25ms sleep, _key_available returns False (no trailing bytes).
        key_available_sequence = iter([True, False])
        read_available_returns = iter([chr(27)])

        def fake_key_available():
            try:
                return next(key_available_sequence)
            except StopIteration:
                return False

        def fake_read_available():
            try:
                return next(read_available_returns)
            except StopIteration:
                return ""

        with (
            patch.object(listener, "_is_unix_tty", return_value=True),
            patch.object(listener, "_setup_terminal"),
            patch.object(listener, "_restore_terminal"),
            patch.object(listener, "_key_available", side_effect=fake_key_available),
            patch.object(listener, "_read_available", side_effect=fake_read_available),
        ):

            async def patched_sleep(duration):
                pass  # no-op: skip real sleep in tests

            with patch.object(asyncio, "sleep", side_effect=patched_sleep):
                await listener.listen(on_escape=on_escape, poll_interval=0.01)

        # The escape callback MUST have been called for standalone ESC.
        on_escape.assert_called_once()
