"""Tests for plugin name validation and anti-impersonation."""

from koder_agent.harness.plugins.name_validation import (
    OFFICIAL_PREFIXES,
    RESERVED_NAMES,
    sanitize_plugin_name,
    validate_plugin_name,
)


class TestValidatePluginName:
    """Tests for validate_plugin_name function."""

    def test_official_prefix_blocked_for_non_official_plugins(self):
        """Non-official plugins cannot use official prefixes."""
        for prefix in ["koder-", "koder_", "official-"]:
            valid, reason = validate_plugin_name(f"{prefix}myplugin", is_official=False)
            assert not valid
            assert "official prefix" in reason.lower()

    def test_reserved_names_blocked(self):
        """Reserved names cannot be used by any plugin."""
        for name in ["koder", "koder-core", "koder-official"]:
            valid, reason = validate_plugin_name(name, is_official=False)
            assert not valid
            assert "reserved" in reason.lower()

            # Even official plugins cannot use reserved names
            valid, reason = validate_plugin_name(name, is_official=True)
            assert not valid
            assert "reserved" in reason.lower()

    def test_valid_names_pass(self):
        """Valid plugin names are accepted."""
        valid_names = [
            "my-plugin",
            "my_plugin",
            "plugin123",
            "awesome-tool-v2",
            "tool_name_here",
            "a",
            "plugin-1-2-3",
        ]
        for name in valid_names:
            valid, reason = validate_plugin_name(name, is_official=False)
            assert valid, f"Name '{name}' should be valid but got: {reason}"
            assert reason == ""

    def test_invalid_characters_caught(self):
        """Plugin names with invalid characters are rejected."""
        invalid_names = [
            "my plugin",  # space
            "my@plugin",  # special char
            "my.plugin",  # dot
            "my/plugin",  # slash
            "my\\plugin",  # backslash
            "my plugin!",  # exclamation
            "plugin#name",  # hash
            "plugin$name",  # dollar
            "plugin%name",  # percent
            "plugin&name",  # ampersand
        ]
        for name in invalid_names:
            valid, reason = validate_plugin_name(name, is_official=False)
            assert not valid, f"Name '{name}' should be invalid"
            assert "invalid characters" in reason.lower()

    def test_empty_name_caught(self):
        """Empty plugin names are rejected."""
        valid, reason = validate_plugin_name("", is_official=False)
        assert not valid
        assert "empty" in reason.lower()

        valid, reason = validate_plugin_name("   ", is_official=False)
        assert not valid
        assert "empty" in reason.lower()

    def test_official_plugins_can_use_official_prefix(self):
        """Official plugins are allowed to use official prefixes."""
        for prefix in ["koder-", "koder_", "official-"]:
            valid, reason = validate_plugin_name(f"{prefix}myplugin", is_official=True)
            assert valid
            assert reason == ""


class TestSanitizePluginName:
    """Tests for sanitize_plugin_name function."""

    def test_lowercases_name(self):
        """Plugin names are converted to lowercase."""
        assert sanitize_plugin_name("MyPlugin") == "myplugin"
        assert sanitize_plugin_name("MY-PLUGIN") == "my-plugin"

    def test_strips_whitespace(self):
        """Leading/trailing whitespace is removed."""
        assert sanitize_plugin_name("  my-plugin  ") == "my-plugin"
        assert sanitize_plugin_name("\tmy-plugin\n") == "my-plugin"

    def test_replaces_spaces_with_dashes(self):
        """Internal spaces are replaced with dashes."""
        assert sanitize_plugin_name("my plugin") == "my-plugin"
        assert sanitize_plugin_name("my  plugin  name") == "my-plugin-name"

    def test_combined_sanitization(self):
        """Multiple sanitization rules work together."""
        assert sanitize_plugin_name("  My Cool Plugin  ") == "my-cool-plugin"
        assert sanitize_plugin_name("AWESOME TOOL") == "awesome-tool"


class TestConstants:
    """Tests for module constants."""

    def test_official_prefixes_defined(self):
        """OFFICIAL_PREFIXES contains expected values."""
        assert isinstance(OFFICIAL_PREFIXES, frozenset)
        assert "koder-" in OFFICIAL_PREFIXES
        assert "koder_" in OFFICIAL_PREFIXES
        assert "official-" in OFFICIAL_PREFIXES

    def test_reserved_names_defined(self):
        """RESERVED_NAMES contains expected values."""
        assert isinstance(RESERVED_NAMES, frozenset)
        assert "koder" in RESERVED_NAMES
        assert "koder-core" in RESERVED_NAMES
        assert "koder-official" in RESERVED_NAMES
