"""Desktop notifications via terminal escape sequences."""

import os
import sys
from dataclasses import dataclass


@dataclass
class NotificationConfig:
    """Configuration for desktop notifications."""

    enabled: bool = True
    sound: bool = False


def detect_terminal() -> str:
    """
    Detect the terminal emulator being used.

    Returns:
        "iterm2" for iTerm2
        "kitty" for Kitty terminal
        "generic" for unknown terminals
    """
    # Check for Kitty first (more specific env var)
    if os.environ.get("KITTY_WINDOW_ID"):
        return "kitty"

    # Check for iTerm2
    term_program = os.environ.get("TERM_PROGRAM", "")
    if term_program == "iTerm.app":
        return "iterm2"

    return "generic"


def notify(title: str, message: str, config: NotificationConfig | None = None) -> None:
    """
    Send a desktop notification via terminal escape sequences.

    Args:
        title: Notification title (unused in current implementation)
        message: Notification message to display
        config: Optional notification configuration (defaults to enabled)
    """
    if config is None:
        config = NotificationConfig()

    if not config.enabled:
        return

    terminal = detect_terminal()

    if terminal == "iterm2":
        # iTerm2 escape sequence
        sys.stdout.write(f"\033]9;{message}\007")
        sys.stdout.flush()
    elif terminal == "kitty":
        # Kitty notification protocol
        sys.stdout.write(f"\033]99;i=1:d=0;{message}\033\\")
        sys.stdout.flush()
    else:
        # Generic terminal bell
        sys.stdout.write("\007")
        sys.stdout.flush()
