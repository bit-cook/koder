"""Extract ``@file`` and ``@server:uri`` mentions from user input and inline their contents."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

_AT_MENTION_RE = re.compile(r'@"([^"]+)"|@(\S+)')

# Matches ``server:protocol://...`` — the colon-then-scheme separator
# distinguishes MCP resource references from plain file paths.
_MCP_RESOURCE_RE = re.compile(r"^([A-Za-z0-9_.-]+):([a-zA-Z][a-zA-Z0-9+.-]*://.*)$")

MAX_FILE_CHARS = 100_000

logger = logging.getLogger(__name__)


def _is_mcp_resource(mention: str) -> bool:
    """Return True if *mention* looks like ``server:protocol://path``."""
    return _MCP_RESOURCE_RE.match(mention) is not None


def _parse_mcp_resource(mention: str) -> tuple[str, str] | None:
    """Parse ``server:protocol://path`` into ``(server_name, uri)`` or *None*."""
    m = _MCP_RESOURCE_RE.match(mention)
    if m is None:
        return None
    return m.group(1), m.group(2)


def extract_at_file_mentions(
    user_input: str,
    active_agent_names: set[str] | None = None,
) -> list[str]:
    """Return file paths referenced via ``@path`` in *user_input*.

    Mentions that match an agent name (or end with ``(agent)``) are excluded.
    MCP resource mentions (``@server:protocol://path``) are also excluded.
    """
    agents = active_agent_names or set()
    paths: list[str] = []
    for match in _AT_MENTION_RE.finditer(user_input):
        mention = match.group(1) or match.group(2)
        if not mention:
            continue
        # Skip agent mentions
        if mention in agents:
            continue
        if mention.rstrip().endswith("(agent)"):
            continue
        # Skip if this looks like @agent-<name> (handled by extract_agent_mention)
        if mention.startswith("agent-"):
            continue
        # Skip MCP resource mentions — handled separately
        if _is_mcp_resource(mention):
            continue
        paths.append(mention)
    return paths


def extract_mcp_resource_mentions(
    user_input: str,
    active_agent_names: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Return ``(server_name, uri)`` pairs for MCP resource ``@mentions``.

    A resource mention has the form ``@server:protocol://path``.
    """
    agents = active_agent_names or set()
    resources: list[tuple[str, str]] = []
    for match in _AT_MENTION_RE.finditer(user_input):
        mention = match.group(1) or match.group(2)
        if not mention:
            continue
        if mention in agents:
            continue
        parsed = _parse_mcp_resource(mention)
        if parsed is not None:
            resources.append(parsed)
    return resources


async def _read_mcp_resource(
    server: Any,
    uri: str,
) -> str | None:
    """Read a single resource from an MCP server session.

    Returns the text content or *None* on failure.
    """
    try:
        from pydantic import AnyUrl

        from koder_agent.mcp.runtime_authorization import call_authorized_server_method

        result = await call_authorized_server_method(server, "read_resource", AnyUrl(uri))
        parts: list[str] = []
        for item in result.contents:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
            else:
                blob = getattr(item, "blob", None)
                if blob is not None:
                    parts.append(f"[binary blob, {len(blob)} bytes]")
        return "\n".join(parts) if parts else None
    except Exception as exc:
        logger.debug("Failed to read MCP resource %s: %s", uri, exc)
        return None


def _find_mcp_server(
    server_name: str,
    mcp_servers: list[Any],
) -> Any | None:
    """Find one connected MCP server by its exact raw name."""
    matches: list[Any] = []
    for server in mcp_servers:
        name = getattr(server, "name", None)
        if not name:
            params = getattr(server, "params", None)
            if params is not None:
                name = getattr(params, "name", None)
        if name == server_name:
            matches.append(server)
    return matches[0] if len(matches) == 1 else None


async def _resolve_mcp_resources(
    resources: list[tuple[str, str]],
    mcp_servers: list[Any],
) -> list[str]:
    """Resolve a list of ``(server_name, uri)`` pairs into inline sections."""
    sections: list[str] = []
    for server_name, uri in resources:
        server = _find_mcp_server(server_name, mcp_servers)
        if server is None:
            sections.append(f"[MCP server not found: {server_name}]")
            continue
        content = await _read_mcp_resource(server, uri)
        if content is None:
            sections.append(f"[Failed to read resource: {server_name}:{uri}]")
        else:
            if len(content) > MAX_FILE_CHARS:
                content = (
                    content[:MAX_FILE_CHARS] + f"\n... [truncated, {len(content)} chars total]"
                )
            sections.append(
                f'<resource server="{server_name}" uri="{uri}">\n{content}\n</resource>'
            )
    return sections


def _resolve_file_paths(
    file_paths: list[str],
    cwd: Path,
) -> list[str]:
    """Resolve local file paths into inline sections."""
    sections: list[str] = []
    for rel_path in file_paths:
        abs_path = (cwd / rel_path).resolve()
        if not abs_path.is_file():
            if abs_path.is_dir():
                try:
                    entries = sorted(p.name for p in abs_path.iterdir())[:200]
                    listing = "\n".join(entries)
                    sections.append(
                        f'<file path="{rel_path}" type="directory">\n{listing}\n</file>'
                    )
                except OSError:
                    sections.append(f"[Directory not readable: {rel_path}]")
            else:
                sections.append(f"[File not found: {rel_path}]")
            continue
        try:
            content = abs_path.read_text(encoding="utf-8", errors="ignore")
            if len(content) > MAX_FILE_CHARS:
                content = (
                    content[:MAX_FILE_CHARS] + f"\n... [truncated, {len(content)} chars total]"
                )
            sections.append(f'<file path="{rel_path}">\n{content}\n</file>')
        except OSError as exc:
            sections.append(f"[Error reading {rel_path}: {exc}]")
    return sections


async def async_process_at_mentions(
    user_input: str,
    cwd: Path,
    active_agent_names: set[str] | None = None,
    mcp_servers: list[Any] | None = None,
) -> str:
    """Async version: read ``@file`` and ``@server:uri`` refs, prepend content.

    MCP resource mentions (``@server:protocol://path``) are resolved via the
    matching MCP server's ``resources/read`` capability.  File mentions are
    resolved from the local filesystem.

    Returns the original input unchanged if no mentions are found.
    """
    file_paths = extract_at_file_mentions(user_input, active_agent_names)
    mcp_resources = extract_mcp_resource_mentions(user_input, active_agent_names)

    if not file_paths and not mcp_resources:
        return user_input

    sections: list[str] = []

    # --- MCP resource resolution ---
    if mcp_resources and mcp_servers:
        resource_sections = await _resolve_mcp_resources(mcp_resources, mcp_servers)
        sections.extend(resource_sections)
    elif mcp_resources:
        for server_name, uri in mcp_resources:
            sections.append(f"[MCP server not available: {server_name}:{uri}]")

    # --- File resolution ---
    sections.extend(_resolve_file_paths(file_paths, cwd))

    file_context = "\n\n".join(sections)
    return f"{file_context}\n\nUser request: {user_input}"


def process_at_file_mentions(
    user_input: str,
    cwd: Path,
    active_agent_names: set[str] | None = None,
    mcp_servers: list[Any] | None = None,
) -> str:
    """Read ``@file`` references and prepend their contents to *user_input*.

    This synchronous version resolves only file mentions.  MCP resource
    mentions are reported as unresolved; use :func:`async_process_at_mentions`
    from async callers to resolve MCP resources properly.

    Returns the original input unchanged if no file mentions are found.
    """
    file_paths = extract_at_file_mentions(user_input, active_agent_names)
    mcp_resources = extract_mcp_resource_mentions(user_input, active_agent_names)

    if not file_paths and not mcp_resources:
        return user_input

    sections: list[str] = []

    # MCP resources cannot be resolved synchronously — report as such
    if mcp_resources:
        for server_name, uri in mcp_resources:
            sections.append(f"[MCP server not available: {server_name}:{uri}]")

    # --- File resolution ---
    sections.extend(_resolve_file_paths(file_paths, cwd))

    file_context = "\n\n".join(sections)
    return f"{file_context}\n\nUser request: {user_input}"
