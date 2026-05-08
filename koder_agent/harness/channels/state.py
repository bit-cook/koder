"""Module-level channel state for the current session."""

from __future__ import annotations

from .types import ChannelEntry

_allowed_channels: list[ChannelEntry] = []
_has_dev_channels: bool = False


def get_allowed_channels() -> list[ChannelEntry]:
    """Return the list of channel entries enabled for this session."""
    return list(_allowed_channels)


def set_allowed_channels(entries: list[ChannelEntry]) -> None:
    """Set the channel entries enabled for this session."""
    global _allowed_channels
    _allowed_channels = list(entries)


def get_has_dev_channels() -> bool:
    """Return whether development channels are loaded."""
    return _has_dev_channels


def set_has_dev_channels(value: bool) -> None:
    """Set whether development channels are loaded."""
    global _has_dev_channels
    _has_dev_channels = value


def reset_channel_state() -> None:
    """Reset all channel state (for testing)."""
    global _allowed_channels, _has_dev_channels
    _allowed_channels = []
    _has_dev_channels = False
