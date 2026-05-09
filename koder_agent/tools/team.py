"""Team lifecycle tools for programmatic team management."""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel

from .compat import function_tool


class TeamCreateModel(BaseModel):
    team_name: str
    description: Optional[str] = None


class TeamDeleteModel(BaseModel):
    pass  # No parameters — deletes the current team context


async def _team_create_impl(
    team_name: str,
    description: str | None = None,
    *,
    _team_service=None,
) -> str:
    """Core implementation for team_create tool."""
    if _team_service is None:
        from koder_agent.harness.agents.teams.service import TeamService

        _team_service = TeamService()

    try:
        team_id = _team_service.create_team(
            team_name,
            description=description,
        )
        record = _team_service.get(team_id)
        return json.dumps(
            {
                "status": "created",
                "team_name": team_name,
                "team_id": team_id,
                "config_path": record.config_path,
                "lead_agent_id": record.lead_agent_id,
            }
        )
    except Exception as exc:
        return json.dumps(
            {
                "status": "error",
                "error": str(exc),
            }
        )


async def _team_delete_impl(
    *,
    _team_service=None,
    _team_id: str | None = None,
) -> str:
    """Core implementation for team_delete tool."""
    if _team_service is None:
        from koder_agent.harness.agents.teams.service import TeamService

        _team_service = TeamService()

    if _team_id is None:
        return json.dumps(
            {
                "status": "error",
                "error": "No team context. Create a team first.",
            }
        )

    try:
        team = _team_service.get(_team_id)
        _team_service.delete_team(_team_id)
        return json.dumps(
            {
                "status": "deleted",
                "team_name": team.name,
                "team_id": _team_id,
            }
        )
    except RuntimeError as exc:
        return json.dumps(
            {
                "status": "error",
                "error": str(exc),
            }
        )
    except KeyError:
        return json.dumps(
            {
                "status": "error",
                "error": f"Team not found: {_team_id}",
            }
        )


@function_tool
async def team_create(
    team_name: str,
    description: str | None = None,
) -> str:
    """Create a new agent team for coordinating multiple agents.

    Args:
        team_name: Name for the new team.
        description: Optional team description.
    """
    return await _team_create_impl(team_name=team_name, description=description)


@function_tool
async def team_delete() -> str:
    """Delete the current team. All teammates must be shut down first."""
    return await _team_delete_impl()
