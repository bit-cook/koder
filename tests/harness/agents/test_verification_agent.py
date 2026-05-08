"""Tests for verification agent definition."""

from koder_agent.harness.agents.definitions import BUILTIN_AGENT_DEFINITIONS


def test_verification_agent_exists():
    types = {a.agent_type for a in BUILTIN_AGENT_DEFINITIONS}
    assert "verification" in types


def test_verification_agent_is_read_only():
    agent = next(a for a in BUILTIN_AGENT_DEFINITIONS if a.agent_type == "verification")
    assert "Edit" in (agent.disallowed_tools or [])
    assert "Write" in (agent.disallowed_tools or [])


def test_verification_agent_has_sonnet_model():
    agent = next(a for a in BUILTIN_AGENT_DEFINITIONS if a.agent_type == "verification")
    assert agent.model == "sonnet"
