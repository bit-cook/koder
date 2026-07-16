from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from koder_agent.mcp.prompts import (
    MCPPrompt,
    MCPPromptRegistry,
    MCPPromptResult,
    _find_server_session,
    _parse_prompt_arguments,
    execute_prompt,
    normalize_mcp_name,
)


def test_normalize_mcp_name_basic():
    assert normalize_mcp_name("my server") == "my_server"


def test_normalize_mcp_name_special_chars():
    assert normalize_mcp_name("my.server!v2") == "my_server_v2"


def test_normalize_mcp_name_collapses_underscores():
    assert normalize_mcp_name("a   b") == "a_b"


def test_normalize_mcp_name_truncates_to_64():
    long_name = "x" * 100
    assert len(normalize_mcp_name(long_name)) <= 64


def test_prompt_command_name():
    prompt = MCPPrompt(
        server_name="github",
        prompt_name="list_prs",
        description="List pull requests",
    )
    assert prompt.command_name == "mcp__github__list_prs"


def test_prompt_command_name_with_normalization():
    prompt = MCPPrompt(
        server_name="my server",
        prompt_name="do something",
    )
    assert prompt.command_name == "mcp__my_server__do_something"


def test_registry_register_and_get():
    reg = MCPPromptRegistry()
    prompt = MCPPrompt(server_name="gh", prompt_name="list_prs")
    reg.register(prompt)
    assert reg.get("mcp__gh__list_prs") is prompt


def test_registry_collision_keeps_first_prompt():
    reg = MCPPromptRegistry()
    first = MCPPrompt(server_name="same server", prompt_name="same prompt", description="first")
    second = MCPPrompt(server_name="same.server", prompt_name="same.prompt", description="second")

    assert reg.register(first) is True
    assert reg.register(second) is False
    assert reg.get(first.command_name) is first


def test_registry_list():
    reg = MCPPromptRegistry()
    reg.register(MCPPrompt(server_name="gh", prompt_name="list_prs"))
    reg.register(MCPPrompt(server_name="gh", prompt_name="create_issue"))
    assert len(reg.list_prompts()) == 2


def test_registry_clear_server():
    reg = MCPPromptRegistry()
    reg.register(MCPPrompt(server_name="gh", prompt_name="list_prs"))
    reg.register(MCPPrompt(server_name="jira", prompt_name="get_issue"))
    count = reg.clear_server("gh")
    assert count == 1
    assert len(reg.list_prompts()) == 1
    assert reg.list_prompts()[0].server_name == "jira"


def test_registry_get_nonexistent():
    reg = MCPPromptRegistry()
    assert reg.get("mcp__nonexistent__foo") is None


# ---------------------------------------------------------------------------
# _parse_prompt_arguments
# ---------------------------------------------------------------------------


def test_parse_positional_args():
    prompt = MCPPrompt(
        server_name="gh",
        prompt_name="review",
        arguments=[{"name": "owner", "required": True}, {"name": "repo", "required": True}],
    )
    result = _parse_prompt_arguments(prompt, ["acme", "widgets"])
    assert result == {"owner": "acme", "repo": "widgets"}


def test_parse_keyword_args():
    prompt = MCPPrompt(
        server_name="gh",
        prompt_name="review",
        arguments=[{"name": "owner", "required": True}, {"name": "repo", "required": True}],
    )
    result = _parse_prompt_arguments(prompt, ["repo=widgets", "owner=acme"])
    assert result == {"owner": "acme", "repo": "widgets"}


def test_parse_mixed_args():
    prompt = MCPPrompt(
        server_name="gh",
        prompt_name="review",
        arguments=[{"name": "owner", "required": True}, {"name": "repo", "required": True}],
    )
    result = _parse_prompt_arguments(prompt, ["acme", "repo=widgets"])
    assert result == {"owner": "acme", "repo": "widgets"}


def test_parse_extra_positional_appended_to_last():
    prompt = MCPPrompt(
        server_name="gh",
        prompt_name="search",
        arguments=[{"name": "query", "required": True}],
    )
    result = _parse_prompt_arguments(prompt, ["hello", "world", "test"])
    assert result == {"query": "hello world test"}


def test_parse_no_args():
    prompt = MCPPrompt(server_name="gh", prompt_name="status", arguments=[])
    result = _parse_prompt_arguments(prompt, [])
    assert result == {}


# ---------------------------------------------------------------------------
# _find_server_session
# ---------------------------------------------------------------------------


def _make_server(name: str, *, has_session: bool = True):
    """Create a mock MCP server with optional session."""
    server = MagicMock()
    server.name = name
    if has_session:
        server.session = MagicMock()
    else:
        del server.session
    return server


def test_find_server_session_by_name():
    s1 = _make_server("github")
    s2 = _make_server("jira")
    session = _find_server_session("github", [s1, s2])
    assert session is s1.session


def test_find_server_session_by_normalized_name():
    s1 = _make_server("my.server")
    session = _find_server_session("my_server", [s1])
    assert session is s1.session


def test_find_server_session_prefers_exact_raw_name_over_normalized_match():
    normalized_match = _make_server("alpha.beta")
    exact_match = _make_server("alpha_beta")

    session = _find_server_session("alpha_beta", [normalized_match, exact_match])

    assert session is exact_match.session


def test_find_server_session_rejects_ambiguous_normalized_fallback():
    first = _make_server("alpha.beta")
    second = _make_server("alpha_beta")

    session = _find_server_session("alpha beta", [first, second])

    assert session is None


def test_find_server_session_rejects_duplicate_exact_raw_names():
    first = _make_server("shared")
    second = _make_server("shared")

    session = _find_server_session("shared", [first, second])

    assert session is None


def test_find_server_session_not_found():
    s1 = _make_server("github")
    session = _find_server_session("nonexistent", [s1])
    assert session is None


def test_find_server_session_no_session_attr():
    s1 = _make_server("github", has_session=False)
    session = _find_server_session("github", [s1])
    assert session is None


def test_find_server_via_params_name():
    """Server with no .name but has params.name."""
    server = MagicMock(spec=[])
    server.name = ""
    server.params = MagicMock()
    server.params.name = "github"
    server.session = MagicMock()
    session = _find_server_session("github", [server])
    assert session is server.session


# ---------------------------------------------------------------------------
# execute_prompt
# ---------------------------------------------------------------------------


@dataclass
class _FakeContent:
    text: str


@dataclass
class _FakeMessage:
    role: str
    content: _FakeContent


@dataclass
class _FakeGetPromptResult:
    messages: list
    description: str = ""


@pytest.mark.asyncio
async def test_execute_prompt_success():
    prompt = MCPPrompt(
        server_name="gh",
        prompt_name="review",
        arguments=[{"name": "repo", "required": True}],
    )
    fake_result = _FakeGetPromptResult(
        messages=[
            _FakeMessage(role="user", content=_FakeContent(text="Review this repo")),
            _FakeMessage(role="assistant", content=_FakeContent(text="Looks good")),
        ],
        description="Code review prompt",
    )
    server = _make_server("gh")
    server.session.get_prompt = AsyncMock(return_value=fake_result)

    result = await execute_prompt(prompt, [server], ["myrepo"])

    assert isinstance(result, MCPPromptResult)
    assert result.description == "Code review prompt"
    assert len(result.messages) == 2
    assert result.messages[0] == {"role": "user", "content": "Review this repo"}
    assert result.messages[1] == {"role": "assistant", "content": "Looks good"}
    server.session.get_prompt.assert_awaited_once_with("review", arguments={"repo": "myrepo"})


@pytest.mark.asyncio
async def test_execute_prompt_no_arguments():
    prompt = MCPPrompt(server_name="gh", prompt_name="status", arguments=[])
    fake_result = _FakeGetPromptResult(
        messages=[_FakeMessage(role="user", content=_FakeContent(text="Get status"))],
    )
    server = _make_server("gh")
    server.session.get_prompt = AsyncMock(return_value=fake_result)

    result = await execute_prompt(prompt, [server], [])

    assert len(result.messages) == 1
    server.session.get_prompt.assert_awaited_once_with("status", arguments=None)


@pytest.mark.asyncio
async def test_execute_prompt_server_not_found():
    prompt = MCPPrompt(server_name="nonexistent", prompt_name="test")
    server = _make_server("gh")

    with pytest.raises(RuntimeError, match="No active session found"):
        await execute_prompt(prompt, [server], [])


@pytest.mark.asyncio
async def test_execute_prompt_session_error():
    prompt = MCPPrompt(server_name="gh", prompt_name="bad")
    server = _make_server("gh")
    server.session.get_prompt = AsyncMock(side_effect=Exception("connection lost"))

    with pytest.raises(RuntimeError, match="Failed to get prompt"):
        await execute_prompt(prompt, [server], [])
