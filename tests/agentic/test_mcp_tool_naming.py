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

from agents import FunctionTool
from mcp.types import Tool as MCPTool

from koder_agent.agentic import agent as agent_module
from koder_agent.agentic.agent import create_dev_agent


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


def _fake_snapshot(model_override=None):
    return {
        "model_name": "gpt-4o",
        "native_openai": True,
        "litellm_kwargs": {},
    }


def _build_agent_with_servers(monkeypatch, servers):
    # Do NOT set KODER_SIMPLE (it hard-forces mcp_servers=[] and skips
    # load_mcp_servers entirely). Instead stub load_mcp_servers to inject fakes.
    monkeypatch.delenv("KODER_SIMPLE", raising=False)
    monkeypatch.setattr(agent_module, "get_model_client_snapshot", _fake_snapshot)

    async def _fake_load():
        return list(servers)

    monkeypatch.setattr(agent_module, "load_mcp_servers", _fake_load)
    return asyncio.run(create_dev_agent([]))


def _mcp_tool_names(agent) -> list[str]:
    return [t.name for t in agent.tools if isinstance(t, FunctionTool)]


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
