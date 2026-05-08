"""Runtime version helpers for harness-owned commands and CLI surfaces."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version

from koder_agent import __version__


def resolve_runtime_version_info() -> tuple[str, str]:
    """Return the runtime version and the source used to resolve it."""
    try:
        return package_version("koder"), "installed-package"
    except PackageNotFoundError:
        return (__version__ or "unknown"), "package-fallback"


def resolve_runtime_version() -> str:
    """Return the version of the runtime currently executing this session."""
    return resolve_runtime_version_info()[0]


def render_cli_version_banner() -> str:
    """Render the top-level `-v/--version` banner."""
    return f"{resolve_runtime_version()} (Koder)"


def render_command_version() -> str:
    """Render the interactive `/version` output."""
    resolved, source = resolve_runtime_version_info()
    build_time = os.environ.get("KODER_BUILD_TIME")
    return "\n".join(
        [
            f"version: {resolved}",
            "package: koder",
            f"source: {source}",
            f"build_time: {build_time or 'unavailable'}",
            f"cli_banner: {render_cli_version_banner()}",
        ]
    )
