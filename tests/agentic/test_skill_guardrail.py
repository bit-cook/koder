"""Tests for the skill restriction guardrail error message (M13 fix)."""

from __future__ import annotations

import asyncio

import pytest

from koder_agent.agentic.skill_guardrail import skill_tool_restriction_guardrail
from koder_agent.tools.skill import Skill
from koder_agent.tools.skill_context import (
    add_skill_restrictions,
    begin_skill_restriction_scope,
    clear_restrictions,
    reset_skill_restriction_scope,
)


@pytest.fixture(autouse=True)
def _clean_restrictions():
    """Ensure each test starts and ends with a clean restriction state."""
    clear_restrictions()
    yield
    clear_restrictions()


def _skill(name: str, allowed: list[str]) -> Skill:
    return Skill(
        name=name,
        description="test",
        content="test skill",
        source="test",
        allowed_tools=allowed,
    )


class _FakeContext:
    def __init__(self, tool_name: str, tool_arguments=None):
        self.tool_name = tool_name
        self.tool_arguments = tool_arguments


class _FakeData:
    def __init__(self, tool_name: str, tool_arguments=None):
        self.context = _FakeContext(tool_name, tool_arguments)


def _get_behavior(result):
    """Extract behavior dict/object from guardrail result."""
    b = result.behavior
    if isinstance(b, dict):
        return b
    return {"type": getattr(b, "type", None), "message": getattr(b, "message", None)}


def test_rejection_message_does_not_promise_clearing_restrictions():
    """The rejection message must NOT claim loading an unrestricted skill clears limits."""

    async def scenario():
        token = begin_skill_restriction_scope()
        try:
            add_skill_restrictions(_skill("my-skill", ["read_file"]))
            data = _FakeData("run_shell")
            result = skill_tool_restriction_guardrail(data)
            behavior = _get_behavior(result)
            # Should be rejected
            assert behavior["type"] == "reject_content"
            msg = behavior["message"]
            # The old misleading message should not be present
            assert "clear these limits" not in msg
            assert "load a skill without restrictions" not in msg
            # Should still mention the blocked tool and the skill
            assert "run_shell" in msg
            assert "my-skill" in msg
        finally:
            reset_skill_restriction_scope(token)

    asyncio.run(scenario())


def test_rejection_message_lists_allowed_tools():
    """The rejection message should list allowed tools and the blocked tool."""

    async def scenario():
        token = begin_skill_restriction_scope()
        try:
            add_skill_restrictions(_skill("test-skill", ["read_file", "glob_search"]))
            data = _FakeData("run_shell")
            result = skill_tool_restriction_guardrail(data)
            behavior = _get_behavior(result)
            assert behavior["type"] == "reject_content"
            info = result.output_info or {}
            assert info.get("blocked_tool") == "run_shell"
            assert "read_file" in info.get("allowed_tools", [])
            assert "glob_search" in info.get("allowed_tools", [])
        finally:
            reset_skill_restriction_scope(token)

    asyncio.run(scenario())


def test_allowed_tool_passes_guardrail():
    """A tool in the allowed list should pass the guardrail."""

    async def scenario():
        token = begin_skill_restriction_scope()
        try:
            add_skill_restrictions(_skill("test-skill", ["read_file"]))
            data = _FakeData("read_file")
            result = skill_tool_restriction_guardrail(data)
            behavior = _get_behavior(result)
            assert behavior["type"] == "allow"
        finally:
            reset_skill_restriction_scope(token)

    asyncio.run(scenario())
