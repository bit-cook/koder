"""Channels: push events into a running session from MCP servers."""

from .gate import ChannelGateResult, find_channel_entry, gate_channel_server
from .notification import (
    CHANNEL_NOTIFICATION_METHOD,
    CHANNEL_PERMISSION_METHOD,
    CHANNEL_PERMISSION_REQUEST_METHOD,
    CHANNEL_TAG,
    ChannelNotificationRouter,
    escape_xml_attr,
    wrap_channel_message,
)
from .permissions import (
    ChannelPermissionCallbacks,
    create_channel_permission_callbacks,
    short_request_id,
    truncate_for_preview,
)
from .state import (
    get_allowed_channels,
    get_has_dev_channels,
    reset_channel_state,
    set_allowed_channels,
    set_has_dev_channels,
)
from .types import ChannelEntry, ChannelEntryPlugin, ChannelEntryServer, parse_channel_entries

__all__ = [
    "CHANNEL_NOTIFICATION_METHOD",
    "CHANNEL_PERMISSION_METHOD",
    "CHANNEL_PERMISSION_REQUEST_METHOD",
    "CHANNEL_TAG",
    "ChannelEntry",
    "ChannelEntryPlugin",
    "ChannelEntryServer",
    "ChannelGateResult",
    "ChannelNotificationRouter",
    "ChannelPermissionCallbacks",
    "create_channel_permission_callbacks",
    "escape_xml_attr",
    "find_channel_entry",
    "gate_channel_server",
    "get_allowed_channels",
    "get_has_dev_channels",
    "parse_channel_entries",
    "reset_channel_state",
    "set_allowed_channels",
    "set_has_dev_channels",
    "short_request_id",
    "truncate_for_preview",
    "wrap_channel_message",
]
