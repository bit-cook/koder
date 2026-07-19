"""Async keyboard listener for ESC key interruption.

This module provides non-blocking keyboard detection for cancelling
agent operations during streaming execution.
"""

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Optional, TypeVar

# Platform-specific imports for Unix terminal control
if sys.platform != "win32":
    import select
    import termios
    import tty

T = TypeVar("T")


class CancellationToken:
    """Token to signal cancellation across async operations."""

    def __init__(self):
        self._cancelled = False
        self._event = asyncio.Event()

    def cancel(self) -> None:
        """Signal cancellation."""
        self._cancelled = True
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Check if cancelled."""
        return self._cancelled

    async def wait(self) -> None:
        """Wait until cancelled."""
        await self._event.wait()


class KeyboardListener:
    """Non-blocking async keyboard listener for detecting ESC key.

    Uses Unix termios/tty/select for character-by-character input detection
    without blocking the event loop.
    """

    ESC_KEY = 27  # ASCII code for ESC

    def __init__(self):
        self._original_settings: Optional[list] = None
        self._listening = False

    def _is_unix_tty(self) -> bool:
        """Check if running on Unix with a TTY stdin."""
        return sys.platform != "win32" and sys.stdin.isatty()

    def _setup_terminal(self) -> None:
        """Set terminal to cbreak mode for character-by-character input."""
        if self._is_unix_tty():
            self._original_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())

    def _restore_terminal(self) -> None:
        """Restore original terminal settings."""
        if self._original_settings is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._original_settings)
            self._original_settings = None

    def _key_available(self) -> bool:
        """Check if a key is available without blocking."""
        if not self._is_unix_tty():
            return False
        return select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], [])

    def _read_available(self) -> str:
        """Read all currently available characters from stdin."""
        chars = []
        while self._key_available():
            chars.append(sys.stdin.read(1))
        return "".join(chars)

    async def listen(
        self,
        on_escape: Callable[[], Awaitable[None]],
        poll_interval: float = 0.05,
    ) -> None:
        """Listen for ESC key presses and invoke callback when detected.

        Args:
            on_escape: Async callback to invoke when ESC is pressed
            poll_interval: How often to check for key presses (seconds)
        """
        if not self._is_unix_tty():
            # On non-TTY or Windows, ESC listening is disabled - return immediately
            return

        self._listening = True
        try:
            self._setup_terminal()
            while self._listening:
                if self._key_available():
                    # Read all available characters at once
                    sequence = self._read_available()
                    # Only trigger on standalone ESC (not escape sequences like arrows)
                    # Arrow keys: ESC[A, ESC[B, etc. Function keys: ESC[15~, etc.
                    if sequence == chr(self.ESC_KEY):
                        # Wait briefly to see if this is the start of a multi-byte
                        # escape sequence split across reads (common in SSH/tmux where
                        # network latency can separate ESC from the trailing bytes).
                        await asyncio.sleep(0.025)  # 25ms disambiguation window
                        if self._key_available():
                            # More bytes arrived — this is an escape sequence (e.g.
                            # arrow key ESC[A), not a standalone ESC press.
                            self._read_available()  # consume and discard
                            continue
                        # No follow-up bytes: confirmed standalone ESC.
                        self._listening = False
                        await on_escape()
                        break
                await asyncio.sleep(poll_interval)
        finally:
            self._restore_terminal()

    def stop(self) -> None:
        """Stop listening for key presses."""
        self._listening = False


async def iter_with_cancellation(
    async_iter: AsyncIterator[T],
    token: CancellationToken,
) -> AsyncIterator[T]:
    """Wrap an async iterator to support cancellation.

    This allows breaking out of an async for loop when cancellation is requested,
    even if the underlying iterator is blocked waiting for the next item.

    Args:
        async_iter: The async iterator to wrap
        token: Cancellation token to check

    Yields:
        Items from the underlying iterator until cancelled
    """
    try:
        while not token.is_cancelled:
            # Create a task for getting the next item
            next_task = asyncio.create_task(async_iter.__anext__())
            cancel_task = asyncio.create_task(token.wait())
            try:
                # Wait for either next item or cancellation
                done, _pending = await asyncio.wait(
                    [next_task, cancel_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                # Retrieve both child outcomes before choosing precedence. If
                # cancellation and iterator failure complete together, the
                # cancellation wins but the iterator exception is still observed.
                if not next_task.done():
                    next_task.cancel()
                if not cancel_task.done():
                    cancel_task.cancel()
                next_result, cancel_result = await asyncio.gather(
                    next_task,
                    cancel_task,
                    return_exceptions=True,
                )

                # Check what completed
                if cancel_task in done or token.is_cancelled:
                    # Cancellation requested
                    break

                if next_task in done:
                    if isinstance(next_result, StopAsyncIteration):
                        break
                    if isinstance(next_result, BaseException):
                        raise next_result
                    yield next_result

                if isinstance(cancel_result, BaseException) and not isinstance(
                    cancel_result,
                    asyncio.CancelledError,
                ):
                    raise cancel_result

            except asyncio.CancelledError:
                next_task.cancel()
                cancel_task.cancel()
                await asyncio.gather(next_task, cancel_task, return_exceptions=True)
                raise
    except StopAsyncIteration:
        pass


@asynccontextmanager
async def escape_listener(
    on_escape: Callable[[], Awaitable[None]],
    enabled: bool = True,
):
    """Context manager that runs an ESC key listener as a background task.

    Usage:
        async with escape_listener(on_escape=handle_cancel):
            async for event in stream:
                process(event)

    Args:
        on_escape: Async callback to invoke when ESC is pressed
        enabled: Whether to enable the listener (disabled for non-TTY)

    Yields:
        KeyboardListener instance (or None if disabled)
    """
    if not enabled:
        yield None
        return

    listener = KeyboardListener()
    task = asyncio.create_task(listener.listen(on_escape))

    try:
        yield listener
    finally:
        listener.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
