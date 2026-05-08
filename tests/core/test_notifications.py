"""Tests for desktop notifications."""

import os
from unittest.mock import patch

from koder_agent.core.notifications import (
    NotificationConfig,
    detect_terminal,
    notify,
)


class TestTerminalDetection:
    """Test terminal detection logic."""

    def test_detect_iterm2(self):
        """Test detection of iTerm2 via TERM_PROGRAM."""
        with patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=True):
            assert detect_terminal() == "iterm2"

    def test_detect_kitty(self):
        """Test detection of Kitty via KITTY_WINDOW_ID."""
        with patch.dict(os.environ, {"KITTY_WINDOW_ID": "1"}, clear=True):
            assert detect_terminal() == "kitty"

    def test_detect_generic_fallback(self):
        """Test generic fallback when no known terminal detected."""
        with patch.dict(os.environ, {}, clear=True):
            assert detect_terminal() == "generic"

    def test_kitty_takes_precedence_over_term_program(self):
        """Test that KITTY_WINDOW_ID takes precedence."""
        with patch.dict(
            os.environ,
            {"KITTY_WINDOW_ID": "1", "TERM_PROGRAM": "iTerm.app"},
            clear=True,
        ):
            assert detect_terminal() == "kitty"


class TestNotify:
    """Test notification sending."""

    def test_notify_iterm2_escape_sequence(self, capsys):
        """Test iTerm2 notification escape sequence."""
        with patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=True):
            notify("Test Title", "Test Message")
            captured = capsys.readouterr()
            assert "\033]9;Test Message\007" in captured.out

    def test_notify_kitty_escape_sequence(self, capsys):
        """Test Kitty notification escape sequence."""
        with patch.dict(os.environ, {"KITTY_WINDOW_ID": "1"}, clear=True):
            notify("Test Title", "Test Message")
            captured = capsys.readouterr()
            assert "\033]99;i=1:d=0;Test Message\033\\" in captured.out

    def test_notify_generic_bell(self, capsys):
        """Test generic terminal bell fallback."""
        with patch.dict(os.environ, {}, clear=True):
            notify("Test Title", "Test Message")
            captured = capsys.readouterr()
            assert "\007" in captured.out

    def test_notify_disabled(self, capsys):
        """Test that disabled notifications produce no output."""
        config = NotificationConfig(enabled=False)
        with patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=True):
            notify("Test Title", "Test Message", config=config)
            captured = capsys.readouterr()
            assert captured.out == ""
            assert captured.err == ""

    def test_notify_with_sound_enabled(self, capsys):
        """Test notification with sound enabled (currently no-op)."""
        config = NotificationConfig(enabled=True, sound=True)
        with patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"}, clear=True):
            notify("Test Title", "Test Message", config=config)
            captured = capsys.readouterr()
            # Sound config accepted but doesn't change output
            assert "\033]9;Test Message\007" in captured.out


class TestNotificationConfig:
    """Test NotificationConfig dataclass."""

    def test_config_defaults(self):
        """Test default configuration values."""
        config = NotificationConfig()
        assert config.enabled is True
        assert config.sound is False

    def test_config_custom_values(self):
        """Test custom configuration values."""
        config = NotificationConfig(enabled=False, sound=True)
        assert config.enabled is False
        assert config.sound is True
