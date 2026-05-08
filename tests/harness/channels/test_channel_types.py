"""Tests for channel entry types and CLI parsing."""

import pytest

from koder_agent.harness.channels.types import (
    ChannelEntryPlugin,
    ChannelEntryServer,
    parse_channel_entries,
)


class TestChannelEntryTypes:
    def test_plugin_entry_defaults(self):
        entry = ChannelEntryPlugin(name="slack", marketplace="anthropic")
        assert entry.kind == "plugin"
        assert entry.name == "slack"
        assert entry.marketplace == "anthropic"
        assert entry.dev is False

    def test_server_entry_defaults(self):
        entry = ChannelEntryServer(name="mybot")
        assert entry.kind == "server"
        assert entry.name == "mybot"
        assert entry.dev is False

    def test_entries_are_frozen(self):
        entry = ChannelEntryPlugin(name="x", marketplace="y")
        with pytest.raises(AttributeError):
            entry.name = "z"  # type: ignore[misc]


class TestParseChannelEntries:
    def test_parse_plugin_entry(self):
        result = parse_channel_entries(["plugin:slack@anthropic"], "--channels")
        assert len(result) == 1
        assert isinstance(result[0], ChannelEntryPlugin)
        assert result[0].name == "slack"
        assert result[0].marketplace == "anthropic"

    def test_parse_server_entry(self):
        result = parse_channel_entries(["server:mybot"], "--channels")
        assert len(result) == 1
        assert isinstance(result[0], ChannelEntryServer)
        assert result[0].name == "mybot"

    def test_parse_mixed_entries(self):
        result = parse_channel_entries(
            ["plugin:telegram@official", "server:webhook"],
            "--channels",
        )
        assert len(result) == 2
        assert isinstance(result[0], ChannelEntryPlugin)
        assert isinstance(result[1], ChannelEntryServer)

    def test_parse_empty_list(self):
        assert parse_channel_entries([], "--channels") == []

    def test_parse_whitespace_only(self):
        assert parse_channel_entries(["  ", ""], "--channels") == []

    def test_parse_minimal_plugin(self):
        result = parse_channel_entries(["plugin:a@b"], "--channels")
        assert result[0].name == "a"
        assert result[0].marketplace == "b"

    def test_parse_plugin_with_multiple_at_signs(self):
        """plugin:name@complex@marketplace — rsplit picks the last @."""
        result = parse_channel_entries(["plugin:name@complex@mkt"], "--channels")
        assert result[0].name == "name@complex"
        assert result[0].marketplace == "mkt"

    def test_malformed_no_prefix(self):
        with pytest.raises(SystemExit):
            parse_channel_entries(["slack"], "--channels")

    def test_malformed_plugin_no_at(self):
        with pytest.raises(SystemExit):
            parse_channel_entries(["plugin:slack"], "--channels")

    def test_malformed_plugin_empty_name(self):
        with pytest.raises(SystemExit):
            parse_channel_entries(["plugin:@anthropic"], "--channels")

    def test_malformed_plugin_empty_marketplace(self):
        with pytest.raises(SystemExit):
            parse_channel_entries(["plugin:slack@"], "--channels")

    def test_malformed_server_empty_name(self):
        with pytest.raises(SystemExit):
            parse_channel_entries(["server:"], "--channels")

    def test_dev_flag_not_set_by_parse(self):
        """parse_channel_entries does not set dev=True; caller does."""
        result = parse_channel_entries(["server:x"], "--channels")
        assert result[0].dev is False
