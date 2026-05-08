"""Centralized runtime-owned filesystem paths for koder."""

from __future__ import annotations

from pathlib import Path


def harness_home_dir() -> Path:
    return Path.home() / ".koder"


def harness_project_dir(cwd: str | Path) -> Path:
    return Path(cwd).resolve() / ".koder"


def user_agents_dir() -> Path:
    return harness_home_dir() / "agents"


def project_agents_dir(cwd: str | Path) -> Path:
    return harness_project_dir(cwd) / "agents"


def settings_path(cwd: str | Path) -> Path:
    return harness_project_dir(cwd) / "settings.json"


def user_agent_memory_dir(agent_type: str) -> Path:
    return harness_home_dir() / "agent-memory" / agent_type


def project_agent_memory_dir(cwd: str | Path, agent_type: str) -> Path:
    return harness_project_dir(cwd) / "agent-memory" / agent_type


def local_agent_memory_dir(cwd: str | Path, agent_type: str) -> Path:
    return harness_project_dir(cwd) / "agent-memory-local" / agent_type


def worktrees_dir(cwd: str | Path) -> Path:
    return harness_project_dir(cwd) / "worktrees"


def teams_root_dir() -> Path:
    return harness_home_dir() / "teams"


def tasks_root_dir() -> Path:
    return harness_home_dir() / "tasks"
