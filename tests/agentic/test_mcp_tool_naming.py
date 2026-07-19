"""Tests for MCP tool exposure in create_dev_agent.

Covers two confirmed defects:

- Fix 7: MCP tools must be exposed to the model with Koder's ``mcp__<server>__<tool>``
  convention (double underscore after ``mcp``), matching the permission service,
  skill ``allowed_tools`` patterns (e.g. ``mcp__playwright__*``), and hook matchers.
  The SDK default exposes bare names (``navigate``) which breaks every rule and can
  crash on cross-server name collisions.
- Fix 6: MCP-derived FunctionTools must carry the same input guardrails
  (plan-mode / skill-restriction / hook) as local tools, so a skill declaring
  ``allowed-tools: [read_file]`` actually blocks MCP tools too.
"""

from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace

import pytest
from agents import FunctionTool
from mcp.types import Tool as MCPTool

from koder_agent.agentic import agent as agent_module
from koder_agent.agentic.agent import _allocate_mcp_tool_names, create_dev_agent
from koder_agent.mcp import MCPServerSet

_SHORT_SHA1_COLLISION_TOOLS = ("a-.+!&&b", "a-::-%/b")


class _FakeMCPServer:
    """Minimal MCP server stub exposing a fixed tool list via list_tools().

    Implements the private hooks ``MCPUtil.to_function_tool`` consults
    (``_get_failure_error_function`` / ``_get_needs_approval_for_tool``) so a
    prefixed FunctionTool can be built without a live server.
    """

    def __init__(self, name: str, tool_names: list[str]):
        self.name = name
        self._tool_names = tool_names
        self.use_structured_content = False
        self.cleanup_count = 0

    async def list_tools(self, *args, **kwargs):
        return [
            MCPTool(
                name=n,
                description=f"{n} tool",
                inputSchema={"type": "object", "properties": {}},
            )
            for n in self._tool_names
        ]

    def _get_failure_error_function(self, failure_error_function):
        return failure_error_function

    def _get_needs_approval_for_tool(self, tool, agent):
        return False

    async def cleanup(self):
        self.cleanup_count += 1


def _fake_snapshot(model_override=None):
    return {
        "model_name": "gpt-4o",
        "native_openai": True,
        "litellm_kwargs": {},
    }


def _build_agent_with_servers_and_tools(monkeypatch, servers, tools):
    # Do NOT set KODER_SIMPLE (it hard-forces mcp_servers=[] and skips
    # load_mcp_servers entirely). Instead stub load_mcp_servers to inject fakes.
    monkeypatch.delenv("KODER_SIMPLE", raising=False)
    monkeypatch.setattr(agent_module, "get_model_client_snapshot", _fake_snapshot)

    async def _fake_load():
        return list(servers)

    monkeypatch.setattr(agent_module, "load_mcp_servers", _fake_load)
    return asyncio.run(create_dev_agent(tools))


def _build_agent_with_servers(monkeypatch, servers):
    return _build_agent_with_servers_and_tools(monkeypatch, servers, [])


def _mcp_tool_names(agent) -> list[str]:
    return [t.name for t in agent.tools if isinstance(t, FunctionTool)]


def _allocate_identities(
    identities: list[tuple[str, str]], reserved_names: set[str] | None = None
) -> dict[tuple[str, str], str]:
    entries = [
        (index, 0, server_name, SimpleNamespace(name=tool_name))
        for index, (server_name, tool_name) in enumerate(identities)
    ]
    allocated = _allocate_mcp_tool_names(entries, reserved_names or set())
    return {identity: allocated[(index, 0)] for index, identity in enumerate(identities)}


def test_mcp_tools_exposed_with_double_underscore_prefix(monkeypatch):
    server = _FakeMCPServer("playwright", ["browser_navigate", "browser_click"])
    agent = _build_agent_with_servers(monkeypatch, [server])

    names = _mcp_tool_names(agent)
    assert "mcp__playwright__browser_navigate" in names
    assert "mcp__playwright__browser_click" in names
    # The bare name must NOT be exposed (that is the SDK default we are fixing).
    assert "browser_navigate" not in names


def test_mcp_servers_not_double_added_by_sdk(monkeypatch):
    """After prefixing, the Agent should not also carry raw mcp_servers that the
    SDK would re-list with bare names (double exposure / collision crash)."""
    server = _FakeMCPServer("playwright", ["navigate"])
    agent = _build_agent_with_servers(monkeypatch, [server])
    assert not agent.mcp_servers


def test_cross_server_same_tool_name_no_collision(monkeypatch):
    """Two servers exposing the same bare tool name must both be reachable under
    their server-namespaced names, instead of raising a duplicate-name error."""
    gh = _FakeMCPServer("github", ["search"])
    gl = _FakeMCPServer("gitlab", ["search"])
    agent = _build_agent_with_servers(monkeypatch, [gh, gl])

    names = _mcp_tool_names(agent)
    assert "mcp__github__search" in names
    assert "mcp__gitlab__search" in names


def test_normalization_collisions_receive_stable_hashes(monkeypatch):
    server = _FakeMCPServer("source", ["a-b", "a_b", "a.b"])

    first = _mcp_tool_names(_build_agent_with_servers(monkeypatch, [server]))
    second = _mcp_tool_names(_build_agent_with_servers(monkeypatch, [server]))

    assert first == second
    assert len(first) == 3
    assert len(set(first)) == 3
    assert all(name.startswith("mcp__source__a_b_") for name in first)


def test_short_sha1_collision_is_stable_in_both_orders():
    identities = [("srv", tool_name) for tool_name in _SHORT_SHA1_COLLISION_TOOLS]

    forward = _allocate_identities(identities)
    reverse = _allocate_identities(list(reversed(identities)))

    assert forward == reverse
    assert len(set(forward.values())) == 2
    assert all(name.startswith("mcp__srv__a______b_cd6ef31d") for name in forward.values())
    assert all(len(name) <= 64 for name in forward.values())


def test_full_sha1_collision_uses_bounded_independent_discriminator(monkeypatch):
    class IdenticalSHA1:
        def hexdigest(self):
            return "0" * 40

    monkeypatch.setattr(agent_module.hashlib, "sha1", lambda _value: IdenticalSHA1())
    identities = [("srv", "a-b"), ("srv", "a_b"), ("srv", "a.b")]

    forward = _allocate_identities(identities)
    reverse = _allocate_identities(list(reversed(identities)))

    assert forward == reverse
    assert len(set(forward.values())) == 3
    assert all(len(name) <= 64 for name in forward.values())


def test_duplicate_tool_names_use_metadata_identity_across_permutations(monkeypatch):
    first = MCPTool(
        name="search",
        description="text search",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    second = MCPTool(
        name="search",
        description="numeric search",
        inputSchema={"type": "object", "properties": {"query": {"type": "integer"}}},
    )

    class DuplicateNameServer(_FakeMCPServer):
        def __init__(self, tools):
            super().__init__("source", [])
            self._tools = tools

        async def list_tools(self, *args, **kwargs):
            return list(self._tools)

    forward_agent = _build_agent_with_servers(monkeypatch, [DuplicateNameServer([first, second])])
    reverse_agent = _build_agent_with_servers(monkeypatch, [DuplicateNameServer([second, first])])
    forward = {tool.description: tool.name for tool in forward_agent.tools}
    reverse = {tool.description: tool.name for tool in reverse_agent.tools}

    assert forward == reverse
    assert len(set(forward.values())) == 2


def test_truly_identical_duplicate_tools_are_deduplicated(monkeypatch):
    duplicate = MCPTool(
        name="search",
        description="same search",
        inputSchema={"type": "object", "properties": {}},
    )

    class IdenticalDuplicateServer(_FakeMCPServer):
        async def list_tools(self, *args, **kwargs):
            return [duplicate, duplicate.model_copy(deep=True)]

    agent = _build_agent_with_servers(monkeypatch, [IdenticalDuplicateServer("source", [])])
    tools = [tool for tool in agent.tools if isinstance(tool, FunctionTool)]

    assert len(tools) == 1
    assert tools[0].name == "mcp__source__search"


def test_tool_name_allocation_is_stable_across_random_permutations():
    long_server = "server_" + "s" * 80
    long_tool = "tool_" + "t" * 100
    identities = [
        ("srv", _SHORT_SHA1_COLLISION_TOOLS[0]),
        ("srv", _SHORT_SHA1_COLLISION_TOOLS[1]),
        ("srv", "a______b"),
        ("source-api", "search"),
        ("source_api", "search"),
        ("source.api", "search"),
        (long_server, long_tool + "a"),
        (long_server, long_tool + "b"),
        ("plain", "unique"),
    ]
    expected = _allocate_identities(identities)
    rng = random.Random(20260714)

    for _ in range(100):
        permutation = identities.copy()
        rng.shuffle(permutation)
        assert _allocate_identities(permutation) == expected

    assert len(set(expected.values())) == len(identities)
    assert all(len(name) <= 64 for name in expected.values())


def test_server_normalization_collision_keeps_both_tools(monkeypatch):
    dashed = _FakeMCPServer("source-api", ["search"])
    underscored = _FakeMCPServer("source_api", ["search"])
    dotted = _FakeMCPServer("source.api", ["search"])

    names = _mcp_tool_names(_build_agent_with_servers(monkeypatch, [dashed, underscored, dotted]))

    assert len(names) == 3
    assert len(set(names)) == 3
    assert all(name.startswith("mcp__source_api__search_") for name in names)


def test_length_truncation_collisions_are_unique_and_bounded(monkeypatch):
    common = "tool_" + "x" * 100
    server = _FakeMCPServer("server_" + "y" * 100, [common + "a", common + "b"])

    names = _mcp_tool_names(_build_agent_with_servers(monkeypatch, [server]))

    assert len(names) == 2
    assert len(set(names)) == 2
    assert all(len(name) <= 64 for name in names)


def test_collision_with_existing_public_tool_is_hashed(monkeypatch):
    server = _FakeMCPServer("source", ["search"])
    existing = FunctionTool(
        name="mcp__source__search",
        description="existing",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=lambda _ctx, _args: "existing",
    )

    agent = _build_agent_with_servers_and_tools(monkeypatch, [server], [existing])
    names = _mcp_tool_names(agent)

    assert "mcp__source__search" in names
    assert len(names) == 2
    assert len(set(names)) == 2
    assert any(name.startswith("mcp__source__search_") for name in names)


def test_reserved_generated_names_are_stable_across_permutations():
    collision_identities = [("srv", tool_name) for tool_name in _SHORT_SHA1_COLLISION_TOOLS]
    baseline = _allocate_identities(collision_identities)
    identities = [*collision_identities, ("source", "search"), ("plain", "unique")]
    reserved_names = {
        baseline[collision_identities[0]],
        "mcp__source__search",
    }
    expected = _allocate_identities(identities, reserved_names)
    rng = random.Random(42)

    for _ in range(50):
        permutation = identities.copy()
        rng.shuffle(permutation)
        assert _allocate_identities(permutation, reserved_names) == expected

    assert expected[collision_identities[0]] != baseline[collision_identities[0]]
    assert expected[("source", "search")] != "mcp__source__search"
    assert not reserved_names.intersection(expected.values())
    assert len(set(expected.values())) == len(identities)
    assert all(len(name) <= 64 for name in expected.values())


def test_mcp_tools_carry_input_guardrails(monkeypatch):
    """Fix 6: MCP FunctionTools must have the skill/plan/hook input guardrails
    attached, matching local tools, so skill allowed_tools restrictions apply."""
    server = _FakeMCPServer("playwright", ["browser_navigate"])
    agent = _build_agent_with_servers(monkeypatch, [server])

    mcp_tool = next(
        t
        for t in agent.tools
        if isinstance(t, FunctionTool) and t.name == "mcp__playwright__browser_navigate"
    )
    guardrails = mcp_tool.tool_input_guardrails or []
    assert guardrails, "MCP tool has no input guardrails attached"
    guardrail_names = {getattr(g, "name", "") for g in guardrails}
    assert "skill_tool_restrictions" in guardrail_names


@pytest.mark.parametrize("failure", [RuntimeError("tools failed"), asyncio.CancelledError()])
def test_agent_tool_construction_failure_closes_temporary_owner(monkeypatch, failure):
    monkeypatch.delenv("KODER_SIMPLE", raising=False)
    monkeypatch.setattr(agent_module, "get_model_client_snapshot", _fake_snapshot)
    server = _FakeMCPServer("owned", [])
    owner = MCPServerSet([server])

    async def fake_load():
        return owner

    async def fail_tools(*args, **kwargs):
        raise failure

    monkeypatch.setattr(agent_module, "load_mcp_servers", fake_load)
    monkeypatch.setattr(agent_module, "_build_prefixed_mcp_tools", fail_tools)

    with pytest.raises(type(failure)):
        asyncio.run(create_dev_agent([]))

    assert server.cleanup_count == 1


def test_agent_constructor_failure_closes_temporary_owner(monkeypatch):
    monkeypatch.delenv("KODER_SIMPLE", raising=False)
    monkeypatch.setattr(agent_module, "get_model_client_snapshot", _fake_snapshot)
    server = _FakeMCPServer("owned", [])
    owner = MCPServerSet([server])

    async def fake_load():
        return owner

    async def no_tools(*args, **kwargs):
        return []

    def fail_agent(*args, **kwargs):
        raise RuntimeError("agent construction failed")

    monkeypatch.setattr(agent_module, "load_mcp_servers", fake_load)
    monkeypatch.setattr(agent_module, "_build_prefixed_mcp_tools", no_tools)
    monkeypatch.setattr(agent_module, "Agent", fail_agent)

    with pytest.raises(RuntimeError, match="agent construction failed"):
        asyncio.run(create_dev_agent([]))

    assert server.cleanup_count == 1
