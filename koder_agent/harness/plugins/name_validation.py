"""Plugin name validation and anti-impersonation protection.

This module provides validation for plugin names to prevent impersonation
of official Koder plugins and ensure naming conventions are followed.
"""

import re

# Protected prefixes that only official plugins can use
OFFICIAL_PREFIXES = frozenset(["koder-", "koder_", "official-"])

# Reserved names that cannot be used by any plugin
RESERVED_NAMES = frozenset(["koder", "koder-core", "koder-official"])

# Pattern for valid plugin names: alphanumeric, dashes, and underscores only
VALID_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_plugin_name(name: str, *, is_official: bool = False) -> tuple[bool, str]:
    """Validate a plugin name against anti-impersonation and naming rules.

    Args:
        name: The plugin name to validate
        is_official: Whether this is an official Koder plugin

    Returns:
        A tuple of (is_valid, error_reason). If valid, error_reason is empty string.
        If invalid, error_reason contains a human-readable explanation.

    Examples:
        >>> validate_plugin_name("my-plugin")
        (True, "")
        >>> validate_plugin_name("koder-official")
        (False, "Name 'koder-official' is reserved and cannot be used")
        >>> validate_plugin_name("koder-myplugin")
        (False, "Name 'koder-myplugin' uses an official prefix but is not an official plugin")
    """
    # Check for empty name
    stripped_name = name.strip()
    if not stripped_name:
        return False, "Plugin name cannot be empty"

    # Check reserved names (blocked for all plugins, even official ones)
    if name in RESERVED_NAMES:
        return False, f"Name '{name}' is reserved and cannot be used"

    # Check official prefixes (only allowed for official plugins)
    if not is_official:
        for prefix in OFFICIAL_PREFIXES:
            if name.startswith(prefix):
                return (
                    False,
                    f"Name '{name}' uses an official prefix but is not an official plugin",
                )

    # Check for invalid characters
    if not VALID_NAME_PATTERN.match(name):
        return (
            False,
            f"Name '{name}' contains invalid characters. Only alphanumeric characters, dashes, and underscores are allowed",
        )

    return True, ""


def sanitize_plugin_name(name: str) -> str:
    """Sanitize a plugin name by normalizing it to standard format.

    This function:
    - Converts to lowercase
    - Strips leading/trailing whitespace
    - Replaces internal spaces with dashes

    Args:
        name: The plugin name to sanitize

    Returns:
        The sanitized plugin name

    Examples:
        >>> sanitize_plugin_name("  My Cool Plugin  ")
        'my-cool-plugin'
        >>> sanitize_plugin_name("AWESOME_TOOL")
        'awesome_tool'
    """
    # Strip whitespace and convert to lowercase
    sanitized = name.strip().lower()

    # Replace spaces with dashes (including multiple consecutive spaces)
    sanitized = re.sub(r"\s+", "-", sanitized)

    return sanitized
