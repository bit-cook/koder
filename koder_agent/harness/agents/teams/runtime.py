"""Runtime helpers for agent-team feature gating and mode resolution."""

from __future__ import annotations

from koder_agent.harness.agents.teams.tmux_backend import (
    TmuxBackend,
    get_current_tmux_session_name,
    get_tmux_session_name,
    is_tmux_available,
)
from koder_agent.harness.config.service import RuntimeConfigService
from koder_agent.harness.paths import tasks_root_dir, teams_root_dir

TEAM_LEAD_NAME = "team-lead"
DEFAULT_TEAMS_ROOT = teams_root_dir()
DEFAULT_TASKS_ROOT = tasks_root_dir()


def default_teams_root():
    return teams_root_dir()


def default_tasks_root():
    return tasks_root_dir()


def resolve_teammate_mode(
    *,
    config_service: RuntimeConfigService | None = None,
    cli_mode: str | None = None,
) -> str:
    """Resolve teammate display mode using CLI override then persisted config."""

    if cli_mode in {"auto", "tmux", "in-process"}:
        return cli_mode
    if config_service is None:
        return "auto"
    mode = config_service.load().harness.teammate_mode
    if mode in {"auto", "tmux", "in-process"}:
        return mode
    return "auto"


def resolve_teammate_execution_mode(mode: str) -> str:
    """Resolve the concrete backend used for teammate execution.

    ``auto`` is intentionally user-facing configuration. The concrete default
    must still provide live team mailbox semantics, so it maps to the
    in-process teammate runner unless the user explicitly requests tmux.
    """

    if mode == "tmux":
        return "tmux"
    return "in-process"


def create_backend(mode: str, team_name: str) -> TmuxBackend | None:
    """Create a backend for the given mode.

    Args:
        mode: Teammate mode ("auto", "tmux", "in-process")
        team_name: Name of the team

    Returns:
        TmuxBackend if mode is "tmux" and tmux is available, else None
    """
    if mode == "tmux":
        if not is_tmux_available():
            raise RuntimeError("Tmux mode requested but tmux is not available")
        session_name = get_current_tmux_session_name() or get_tmux_session_name(team_name)
        return TmuxBackend(session_name=session_name)
    return None
