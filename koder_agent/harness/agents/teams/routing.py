"""Routing helpers for team mailboxes."""

from __future__ import annotations

from koder_agent.harness.agents.messages import AgentMessage


def route_team_message(*, team_id: str, content: str) -> AgentMessage:
    """Create a team-scoped mailbox message."""
    return AgentMessage.create(agent_id=team_id, content=content)
