"""Plugin name validation and anti-impersonation protection.

This module provides validation for plugin names to prevent impersonation
of official Koder plugins and ensure naming conventions are followed.
"""

import re

# Protected prefixes that only official plugins can use
OFFICIAL_PREFIXES = frozenset(["koder-", "koder_", "official-"])

# Reserved names that cannot be used by any plugin. These are compared after
# canonicalization, so the set itself is lowercase.
RESERVED_NAMES = frozenset(
    [
        "koder",
        "koder-core",
        "koder-official",
        "marketplace-cache",
        "marketplaces.json",
        "state.json",
    ]
)

# A plugin name must be one portable path segment. Keep this grammar shared by
# manifest parsing and lifecycle sinks so platform-specific separators and dot
# segments can never become filesystem paths.
MAX_PLUGIN_NAME_LENGTH = 255
VALID_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
VALID_MARKETPLACE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


def canonical_plugin_name(name: object) -> tuple[str | None, str]:
    """Return a portable canonical plugin identity or a validation error.

    Identities are deliberately not normalized at filesystem sinks. Requiring
    callers and manifests to provide the lowercase canonical spelling prevents
    ``Demo`` and ``demo`` from silently addressing the same directory on a
    case-insensitive filesystem.
    """
    if not isinstance(name, str):
        return None, "Plugin name must be a string"
    if not name.strip():
        return None, "Plugin name cannot be empty"
    if name != name.lower():
        return None, f"Plugin name '{name}' must use its lowercase canonical spelling"
    if len(name) > MAX_PLUGIN_NAME_LENGTH:
        return None, f"Plugin name must not exceed {MAX_PLUGIN_NAME_LENGTH} characters"
    windows_stem = name.split(".", 1)[0].upper()
    if windows_stem in _WINDOWS_RESERVED_NAMES:
        return None, f"Plugin name '{name}' is reserved on Windows"
    if not VALID_NAME_PATTERN.fullmatch(name):
        return (
            None,
            "Plugin name contains invalid characters or separator placement; it must start "
            "and end with a lowercase alphanumeric character and contain only lowercase "
            "alphanumeric characters, dots, dashes, and underscores",
        )
    if name in RESERVED_NAMES:
        return None, f"Plugin name '{name}' is reserved for Koder infrastructure"
    return name, ""


def validate_plugin_name_format(name: object) -> tuple[bool, str]:
    """Validate the canonical, cross-platform plugin-name grammar."""
    canonical_name, error = canonical_plugin_name(name)
    return canonical_name is not None, error


def canonical_marketplace_name(name: object) -> tuple[str | None, str]:
    """Return a safe canonical identifier for a marketplace source basename.

    Repository and directory basenames are external names, so mixed case is
    accepted. The stored identifier is lowercase to avoid aliases on
    case-insensitive filesystems while retaining the plugin identity rules as
    a separate, stricter validation path.
    """
    if not isinstance(name, str):
        return None, "Marketplace name must be a string"
    if not name.strip():
        return None, "Marketplace name cannot be empty"
    if len(name) > MAX_PLUGIN_NAME_LENGTH:
        return None, f"Marketplace name must not exceed {MAX_PLUGIN_NAME_LENGTH} characters"
    windows_stem = name.split(".", 1)[0].upper()
    if windows_stem in _WINDOWS_RESERVED_NAMES:
        return None, f"Marketplace name '{name}' is reserved on Windows"
    if not VALID_MARKETPLACE_NAME_PATTERN.fullmatch(name):
        return (
            None,
            "Marketplace name contains invalid characters or separator placement; it must "
            "be one path component containing only ASCII alphanumeric characters, dots, "
            "dashes, and underscores",
        )

    canonical_name = name.lower()
    if canonical_name in RESERVED_NAMES:
        return None, f"Marketplace name '{name}' is reserved for Koder infrastructure"
    for prefix in OFFICIAL_PREFIXES:
        if canonical_name.startswith(prefix):
            return None, f"Marketplace name '{name}' uses an official prefix"
    return canonical_name, ""


def validate_plugin_name(name: object, *, is_official: bool = False) -> tuple[bool, str]:
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
    is_valid, error_reason = validate_plugin_name_format(name)
    if not is_valid:
        return False, error_reason

    # Check official prefixes (only allowed for official plugins)
    if not is_official:
        for prefix in OFFICIAL_PREFIXES:
            if isinstance(name, str) and name.startswith(prefix):
                return (
                    False,
                    f"Name '{name}' uses an official prefix but is not an official plugin",
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
