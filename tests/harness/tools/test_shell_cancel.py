# ruff: noqa: E402
"""Tests for shell process cleanup on cancellation (H13).

Verifies that asyncio.CancelledError during foreground shell execution
kills the process group before propagating, preventing orphaned processes.
"""

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.tools.shell_executor import _run_foreground_unsandboxed


class TestShellCancelCleanup:
    """CancelledError during communicate() must kill the process group."""

    @pytest.mark.asyncio
    async def test_cancelled_error_kills_process_group(self):
        """CancelledError during communicate() must call _kill_process_group."""
        mock_process = AsyncMock()
        # First call raises CancelledError; second call (drain) succeeds silently
        mock_process.communicate = AsyncMock(side_effect=asyncio.CancelledError())
        mock_process.pid = 12345
        mock_process.returncode = None

        with (
            patch(
                "koder_agent.harness.tools.shell_executor.asyncio.create_subprocess_shell",
                return_value=mock_process,
            ),
            patch("koder_agent.harness.tools.shell_executor._kill_process_group") as mock_kill,
        ):
            with pytest.raises(asyncio.CancelledError):
                await _run_foreground_unsandboxed("sleep 300", timeout=120, session_id=None)

            mock_kill.assert_called_once_with(mock_process)

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """CancelledError must re-raise after cleanup so the SDK sees cancellation."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.CancelledError())
        mock_process.pid = 99999
        mock_process.returncode = None

        with (
            patch(
                "koder_agent.harness.tools.shell_executor.asyncio.create_subprocess_shell",
                return_value=mock_process,
            ),
            patch("koder_agent.harness.tools.shell_executor._kill_process_group"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await _run_foreground_unsandboxed("sleep 600", timeout=120, session_id=None)

    @pytest.mark.asyncio
    async def test_timeout_still_works_after_cancel_handler_added(self):
        """TimeoutError path is unaffected by the new CancelledError handler."""
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_process.pid = 11111
        mock_process.returncode = None

        with (
            patch(
                "koder_agent.harness.tools.shell_executor.asyncio.create_subprocess_shell",
                return_value=mock_process,
            ),
            patch("koder_agent.harness.tools.shell_executor._kill_process_group") as mock_kill,
            patch(
                "koder_agent.harness.tools.shell_executor._drain_after_kill",
                return_value=None,
            ),
        ):
            result = await _run_foreground_unsandboxed("sleep 300", timeout=5, session_id=None)

            mock_kill.assert_called_once_with(mock_process)
            assert "timed out" in result.output
