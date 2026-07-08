"""Test that statusline command execution does not block rendering (H15)."""

import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


class TestStatusLineNonBlocking:
    """Verify that get_formatted_text does not block on subprocess calls."""

    def test_render_returns_immediately_while_command_runs(self):
        """get_formatted_text must return in <100ms when re-rendering (non-cold-start).

        The first render with an empty cache runs synchronously (so the status
        line is populated immediately on startup). Subsequent renders with a
        changed signature dispatch to a background thread and must not block.
        """
        from koder_agent.core.status_line import StatusLine

        usage_tracker = MagicMock()
        usage_tracker.model = "test-model"

        sl = StatusLine(usage_tracker, session_id="test-session")
        # Pre-populate cache to simulate a prior successful render (non-cold-start).
        sl._cached_command_output = "old-output"
        sl._cached_command_signature = "old-signature"
        # Set a notice so the custom statusline path returns FormattedText
        # even when the new command hasn't completed yet.
        sl._notice = "loading..."

        # Mock resolve_statusline_config to return a config with a slow command
        config_mock = MagicMock()
        config_mock.command = "sleep 5 && echo done"
        config_mock.padding = 0

        with (
            patch(
                "koder_agent.core.status_line.resolve_statusline_config",
                return_value=config_mock,
            ),
            patch.object(sl, "_build_statusline_payload", return_value={"stub": True}),
        ):
            start = time.monotonic()
            # Signature differs from cached -> dispatches background refresh
            sl.get_formatted_text()
            elapsed = time.monotonic() - start

            # The render callback must NOT wait for the subprocess.
            # It should return in well under 1 second (the subprocess sleeps 5s).
            assert elapsed < 1.0, (
                f"get_formatted_text blocked for {elapsed:.2f}s; "
                f"subprocess should run in background"
            )

    def test_cached_output_returned_on_subsequent_calls(self):
        """After background refresh completes, cached output is used."""
        from koder_agent.core.status_line import StatusLine

        usage_tracker = MagicMock()
        usage_tracker.model = "test-model"
        usage_tracker.total_input_tokens = 1000
        usage_tracker.total_output_tokens = 500
        usage_tracker.total_cost = 0.01
        usage_tracker.last_input_tokens = 100
        usage_tracker.last_output_tokens = 50

        sl = StatusLine(usage_tracker, session_id="test-session")
        # Pre-populate the cache to simulate a completed background refresh
        sl._cached_command_output = "branch: main"
        sl._cached_command_signature = "some-signature"

        config_mock = MagicMock()
        config_mock.command = "echo 'branch: main'"
        config_mock.padding = 0

        with patch(
            "koder_agent.core.status_line.resolve_statusline_config",
            return_value=config_mock,
        ):
            with patch.object(sl, "_build_statusline_payload", return_value={}):
                # Forge same signature so no new subprocess is spawned
                import json

                sl._cached_command_signature = json.dumps(
                    {"command": config_mock.command, "padding": 0, "payload": {}},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                result = sl.get_formatted_text()
                # Should get a FormattedText (not None) from the cached output
                assert result is not None
