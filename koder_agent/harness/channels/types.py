"""Channel entry types and CLI parsing."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class ChannelEntryPlugin:
    """A channel entry backed by a plugin from a marketplace."""

    kind: str = "plugin"
    name: str = ""
    marketplace: str = ""
    dev: bool = False


@dataclass(frozen=True)
class ChannelEntryServer:
    """A channel entry backed by a manually configured MCP server."""

    kind: str = "server"
    name: str = ""
    dev: bool = False


ChannelEntry = Union[ChannelEntryPlugin, ChannelEntryServer]


def parse_channel_entries(raw: list[str], flag_name: str) -> list[ChannelEntry]:
    """Parse raw CLI channel entry strings into typed entries.

    Supported formats:
        - ``plugin:name@marketplace``  →  ChannelEntryPlugin
        - ``server:name``              →  ChannelEntryServer

    Exits with code 1 on malformed entries.
    """
    entries: list[ChannelEntry] = []
    for token in raw:
        token = token.strip()
        if not token:
            continue

        if token.startswith("plugin:"):
            rest = token[len("plugin:") :]
            if "@" not in rest:
                print(
                    f"Error: {flag_name} plugin entry must be plugin:name@marketplace, got: {token}",
                    file=sys.stderr,
                )
                sys.exit(1)
            name, marketplace = rest.rsplit("@", 1)
            if not name or not marketplace:
                print(
                    f"Error: {flag_name} plugin entry has empty name or marketplace: {token}",
                    file=sys.stderr,
                )
                sys.exit(1)
            entries.append(ChannelEntryPlugin(name=name, marketplace=marketplace))

        elif token.startswith("server:"):
            name = token[len("server:") :]
            if not name:
                print(
                    f"Error: {flag_name} server entry has empty name: {token}",
                    file=sys.stderr,
                )
                sys.exit(1)
            entries.append(ChannelEntryServer(name=name))

        else:
            print(
                f"Error: {flag_name} entry must start with plugin: or server:, got: {token}",
                file=sys.stderr,
            )
            sys.exit(1)

    return entries
