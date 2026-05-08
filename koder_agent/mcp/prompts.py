"""MCP prompt discovery and execution."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from agents.mcp import MCPServer

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_mcp_name(name: str) -> str:
    """Normalize a name for MCP command format."""
    result = _SAFE_NAME_RE.sub("_", name)
    # Collapse consecutive underscores
    while "__" in result:
        result = result.replace("__", "_")
    return result.strip("_")[:64]


@dataclass
class MCPPrompt:
    """A prompt discovered from an MCP server."""

    server_name: str
    prompt_name: str
    description: str = ""
    arguments: list[dict] = field(default_factory=list)

    @property
    def command_name(self) -> str:
        """Get the slash command name for this prompt."""
        server = normalize_mcp_name(self.server_name)
        prompt = normalize_mcp_name(self.prompt_name)
        return f"mcp__{server}__{prompt}"


@dataclass
class MCPPromptResult:
    """Result of executing an MCP prompt."""

    messages: list[dict]
    description: str = ""


class MCPPromptRegistry:
    """Registry of discovered MCP prompts."""

    def __init__(self):
        self._prompts: dict[str, MCPPrompt] = {}

    def register(self, prompt: MCPPrompt) -> None:
        self._prompts[prompt.command_name] = prompt

    def get(self, command_name: str) -> MCPPrompt | None:
        return self._prompts.get(command_name)

    def list_prompts(self) -> list[MCPPrompt]:
        return list(self._prompts.values())

    def clear(self) -> None:
        self._prompts.clear()

    def clear_server(self, server_name: str) -> int:
        """Remove all prompts from a specific server. Returns count removed."""
        to_remove = [
            key for key, prompt in self._prompts.items() if prompt.server_name == server_name
        ]
        for key in to_remove:
            del self._prompts[key]
        return len(to_remove)


# Global registry
_registry = MCPPromptRegistry()


def get_prompt_registry() -> MCPPromptRegistry:
    return _registry


def _parse_prompt_arguments(prompt: MCPPrompt, raw_args: list[str]) -> dict[str, str]:
    """Parse CLI positional args into named arguments using the prompt's argument spec.

    Positional args are mapped to argument names in declaration order.
    ``key=value`` tokens are treated as explicit keyword arguments.
    """
    arguments: dict[str, str] = {}
    positional: list[str] = []
    for token in raw_args:
        if "=" in token:
            key, _, value = token.partition("=")
            arguments[key] = value
        else:
            positional.append(token)

    arg_names = [arg["name"] for arg in prompt.arguments]
    for idx, value in enumerate(positional):
        if idx < len(arg_names):
            arguments.setdefault(arg_names[idx], value)
        else:
            # Extra positional args: append to the last argument
            if arg_names:
                last = arg_names[-1]
                arguments[last] = f"{arguments.get(last, '')} {value}".strip()

    return arguments


def _find_server_session(server_name: str, mcp_servers: List[MCPServer]) -> object | None:
    """Find the MCP ClientSession for *server_name* among live servers."""
    normalized = normalize_mcp_name(server_name)
    for server in mcp_servers:
        name = getattr(server, "name", "")
        if not name:
            # MCPServerStdio stores the name in params
            params = getattr(server, "params", None)
            if params is not None:
                name = getattr(params, "name", "")
        # Try matching either the raw name or the normalized form
        if normalize_mcp_name(name) == normalized or name == server_name:
            session = getattr(server, "session", None)
            if session is not None:
                return session
    return None


async def execute_prompt(
    prompt: MCPPrompt,
    mcp_servers: List[MCPServer],
    raw_args: list[str],
) -> MCPPromptResult:
    """Execute an MCP prompt via the server's client session.

    Locates the server session for *prompt.server_name*, calls
    ``session.get_prompt(name, arguments)`` and converts the result
    into an :class:`MCPPromptResult`.

    Raises :class:`RuntimeError` on failure.
    """
    session = _find_server_session(prompt.server_name, mcp_servers)
    if session is None:
        raise RuntimeError(f"No active session found for MCP server '{prompt.server_name}'")

    arguments = _parse_prompt_arguments(prompt, raw_args) or None

    try:
        result = await session.get_prompt(prompt.prompt_name, arguments=arguments)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to get prompt '{prompt.prompt_name}' from server '{prompt.server_name}': {exc}"
        ) from exc

    messages: list[dict] = []
    for msg in result.messages:
        content = msg.content
        # Extract text from typed content objects
        text = getattr(content, "text", None) or str(content)
        messages.append({"role": msg.role, "content": text})

    return MCPPromptResult(
        messages=messages,
        description=result.description or "",
    )
