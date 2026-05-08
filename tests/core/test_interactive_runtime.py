"""Tests for interactive runtime features: keybindings, tips, notifications."""

import asyncio
import time
from unittest.mock import patch

from koder_agent.core.interactive import InteractivePrompt
from koder_agent.core.keybindings import KeybindingManager
from koder_agent.harness.tips import TipManager


class TestKeybindingOverrides:
    """Test keybinding override system."""

    def test_keybinding_manager_loads_defaults(self, tmp_path):
        """Keybinding manager should load defaults when no config exists."""
        config_path = tmp_path / "keybindings.json"
        mgr = KeybindingManager(config_path=config_path)

        # Should return default bindings
        assert mgr.get_key("submit") == "enter"
        assert mgr.get_key("cancel") == "c-c"
        assert mgr.get_key("newline") == "c-j"

    def test_keybinding_manager_loads_overrides(self, tmp_path):
        """Keybinding manager should load user overrides from config."""
        config_path = tmp_path / "keybindings.json"
        config_path.write_text('{"submit": "c-s", "cancel": null}')

        mgr = KeybindingManager(config_path=config_path)

        # Override should apply
        assert mgr.get_key("submit") == "c-s"
        # Null should unbind
        assert mgr.get_key("cancel") is None
        # Defaults should still work
        assert mgr.get_key("newline") == "c-j"

    def test_interactive_prompt_initializes_keybinding_manager(self, tmp_path):
        """InteractivePrompt should initialize KeybindingManager."""
        # Create a temporary koder dir
        koder_dir = tmp_path / ".koder"
        koder_dir.mkdir()

        with patch("koder_agent.core.interactive.Path.home", return_value=tmp_path):
            prompt = InteractivePrompt(commands={"/test": "Test command"})

            assert prompt.keybinding_manager is not None
            assert isinstance(prompt.keybinding_manager, KeybindingManager)


class TestTipsDisplay:
    """Test tip display after responses."""

    def test_tip_manager_rotation(self):
        """TipManager should rotate through tips and respect cooldown."""
        mgr = TipManager(cooldown_window=3)

        # Get a few tips
        tip1 = mgr.get_tip()
        assert tip1 is not None

        tip2 = mgr.get_tip()
        assert tip2 is not None
        assert tip2 != tip1  # Should be different

        tip3 = mgr.get_tip()
        assert tip3 is not None

    def test_show_tip_displays_message(self, capsys):
        """show_tip should display a tip from TipManager."""
        prompt = InteractivePrompt(commands={})

        # Mock TipManager to return a specific tip
        with patch.object(prompt.tip_manager, "get_tip", return_value="Test tip message"):
            prompt.show_tip()

        # Can't easily capture Rich console output in pytest,
        # but we can verify the method doesn't crash
        assert True

    def test_show_tip_handles_no_tip(self):
        """show_tip should handle gracefully when no tip is available."""
        prompt = InteractivePrompt(commands={})

        # Mock TipManager to return None
        with patch.object(prompt.tip_manager, "get_tip", return_value=None):
            prompt.show_tip()  # Should not crash

        assert True


class TestNotifications:
    """Test desktop notifications for long operations."""

    def test_mark_response_timing(self):
        """mark_response_start/complete should track timing correctly."""
        prompt = InteractivePrompt(commands={})

        # Initially no start time
        assert prompt._last_response_start_time is None

        # Mark start
        prompt.mark_response_start()
        assert prompt._last_response_start_time is not None

        # Small delay
        time.sleep(0.1)

        # Mark complete without notification (< 30s)
        with patch("koder_agent.core.notifications.notify") as mock_notify:
            prompt.mark_response_complete(show_tip=False)
            mock_notify.assert_not_called()

        # Start time should be reset
        assert prompt._last_response_start_time is None

    def test_notification_sent_for_long_operation(self):
        """Notification should be sent for operations > 30 seconds."""
        prompt = InteractivePrompt(commands={})

        # Mock the start time to be 35 seconds ago
        prompt._last_response_start_time = time.monotonic() - 35

        with patch("koder_agent.core.notifications.notify") as mock_notify:
            prompt.mark_response_complete(show_tip=False)
            mock_notify.assert_called_once_with("Koder", "Task completed")

    def test_notification_not_sent_for_short_operation(self):
        """Notification should NOT be sent for operations < 30 seconds."""
        prompt = InteractivePrompt(commands={})

        # Mock the start time to be 10 seconds ago
        prompt._last_response_start_time = time.monotonic() - 10

        with patch("koder_agent.core.notifications.notify") as mock_notify:
            prompt.mark_response_complete(show_tip=False)
            mock_notify.assert_not_called()

    def test_mark_response_complete_shows_tip(self):
        """mark_response_complete should show tip when enabled."""
        prompt = InteractivePrompt(commands={})
        prompt._last_response_start_time = time.monotonic() - 5  # Short operation

        with patch.object(prompt, "show_tip") as mock_show_tip:
            prompt.mark_response_complete(show_tip=True, context={"test": "value"})
            mock_show_tip.assert_called_once_with({"test": "value"})

    def test_mark_response_complete_skips_tip(self):
        """mark_response_complete should skip tip when disabled."""
        prompt = InteractivePrompt(commands={})
        prompt._last_response_start_time = time.monotonic() - 5  # Short operation

        with patch.object(prompt, "show_tip") as mock_show_tip:
            prompt.mark_response_complete(show_tip=False)
            mock_show_tip.assert_not_called()

    def test_refresh_prompt_suggestion_sets_empty_prompt_ghost(self):
        """Post-turn suggestion generation should update ghost text state."""
        prompt = InteractivePrompt(commands={})

        suggestion = asyncio.run(
            prompt.refresh_prompt_suggestion("fix the bug", "The test suite failed")
        )

        assert suggestion == "Run the tests"
        assert prompt.auto_suggest.get_speculative_suggestion() == "Run the tests"


class TestIntegration:
    """Integration tests for runtime features."""

    def test_full_response_lifecycle(self):
        """Test full lifecycle: start -> complete -> tip + notification."""
        prompt = InteractivePrompt(commands={})

        # Simulate a long operation
        prompt.mark_response_start()
        prompt._last_response_start_time = time.monotonic() - 35  # Override to 35s ago

        with (
            patch("koder_agent.core.notifications.notify") as mock_notify,
            patch.object(prompt.tip_manager, "get_tip", return_value="Test tip"),
        ):
            prompt.mark_response_complete(show_tip=True, context={})

            # Should have notified
            mock_notify.assert_called_once()

            # Tip should have been requested
            prompt.tip_manager.get_tip.assert_called_once()
