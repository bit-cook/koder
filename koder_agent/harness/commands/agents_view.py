"""Helpers for rendering local /agents command output."""

from __future__ import annotations

from pathlib import Path

from koder_agent.harness.agents.definitions import (
    DISPLAY_SOURCE_GROUPS,
    AgentDefinition,
    AgentDefinitionsResult,
    render_agent_profiles,
)
from koder_agent.harness.agents.models import AgentRecord
from koder_agent.harness.paths import project_agents_dir, user_agents_dir

SOURCE_LABELS = {source: label for source, label in DISPLAY_SOURCE_GROUPS}


def _agent_file_path(agent: AgentDefinition) -> str | None:
    if not agent.filename or not agent.base_dir:
        return None
    return str(Path(agent.base_dir) / f"{agent.filename}.md")


def _agent_tools_display(agent: AgentDefinition) -> str:
    if agent.tools is None:
        return "all"
    if not agent.tools:
        return "none"
    return ", ".join(agent.tools)


def _active_source_by_type(definitions: AgentDefinitionsResult) -> dict[str, str]:
    return {agent.agent_type: agent.source for agent in definitions.active_agents}


def _agent_identity(agent: AgentDefinition) -> tuple[str, str, str | None, str | None]:
    return (agent.agent_type, agent.source, agent.base_dir, agent.filename)


def render_agents_overview(definitions: AgentDefinitionsResult, *, cwd: Path) -> str:
    """Render the default /agents overview."""

    lines = [
        f"user_agents_dir: {user_agents_dir()}",
        f"project_agents_dir: {project_agents_dir(cwd)}",
        render_agent_profiles(definitions.active_agents),
    ]

    active_lookup = _active_source_by_type(definitions)
    shadowed_lines: list[str] = []
    active_set = {_agent_identity(agent) for agent in definitions.active_agents}
    for agent in definitions.all_agents:
        if _agent_identity(agent) in active_set:
            continue
        active_source = active_lookup.get(agent.agent_type)
        if not active_source:
            continue
        label = SOURCE_LABELS.get(active_source, active_source)
        shadowed_lines.append(f"- {agent.agent_type} ({agent.source}) shadowed by {label}")
    if shadowed_lines:
        lines.append("shadowed_agents:")
        lines.extend(shadowed_lines)

    if definitions.failed_files:
        lines.append("failed_files:")
        for item in definitions.failed_files:
            lines.append(f"- {item['path']}: {item['error']}")

    return "\n".join(lines)


def render_agent_details(matches: list[AgentDefinition], *, requested_name: str) -> str:
    """Render /agents show output for one or more matching agents."""

    lines = [f"agents: {requested_name}"]
    for index, agent in enumerate(matches):
        if index:
            lines.append("")
        lines.append(f"source: {agent.source}")
        file_path = _agent_file_path(agent)
        if file_path:
            lines.append(f"path: {file_path}")
        lines.append(f"description: {agent.when_to_use}")
        lines.append(f"tools: {_agent_tools_display(agent)}")
        lines.append(f"model: {agent.model or 'inherit'}")
        if agent.permission_mode:
            lines.append(f"permission_mode: {agent.permission_mode}")
        if agent.memory:
            lines.append(f"memory: {agent.memory}")
        if agent.skills:
            lines.append(f"skills: {', '.join(agent.skills)}")
        if agent.hooks:
            lines.append(f"hooks: {', '.join(sorted(agent.hooks.keys()))}")
        if agent.color:
            lines.append(f"color: {agent.color}")
        if agent.isolation:
            lines.append(f"isolation: {agent.isolation}")
        if agent.max_turns is not None:
            lines.append(f"max_turns: {agent.max_turns}")
        if agent.background is not None:
            lines.append(f"background: {'true' if agent.background else 'false'}")
        if agent.source != "built-in":
            lines.append("system_prompt:")
            lines.append(agent.system_prompt)
    return "\n".join(lines)


def render_agent_runtime_summaries(records: list[AgentRecord]) -> str:
    """Render concise summaries for runtime agent records."""

    if not records:
        return "agents: no runtime agents"
    lines = ["agents: runtime summaries"]
    for record in records:
        summary = record.summary or "No summary yet"
        lines.append(f"- {record.id} {record.profile} {record.state}: {summary}")
    return "\n".join(lines)


def render_agent_runtime_summary(record: AgentRecord) -> str:
    """Render one runtime agent summary."""

    lines = [
        "agents: summary",
        f"agent_id: {record.id}",
        f"profile: {record.profile}",
        f"state: {record.state}",
        f"summary: {record.summary or 'No summary yet'}",
    ]
    if record.summary_updated_at:
        lines.append(f"summary_updated_at: {record.summary_updated_at}")
    if record.description:
        lines.append(f"description: {record.description}")
    if record.permission_mode:
        lines.append(f"permission_mode: {record.permission_mode}")
    if record.model_config:
        lines.append("model_config:")
        for key in (
            "model_override",
            "model_name",
            "provider",
            "base_url",
            "native_openai",
            "api_key_present",
            "reasoning_effort",
            "litellm_model",
            "oauth_provider",
            "oauth_headers_present",
        ):
            if key in record.model_config:
                value = record.model_config[key]
                lines.append(f"  {key}: {value if value is not None else 'none'}")
    if record.session_id:
        lines.append(f"session_id: {record.session_id}")
    if record.output_path:
        lines.append(f"output_file: {record.output_path}")
    if record.worktree_path:
        lines.append(f"worktree_path: {record.worktree_path}")
    if record.worktree_branch:
        lines.append(f"worktree_branch: {record.worktree_branch}")
    if record.error:
        lines.append(f"error: {record.error}")
    return "\n".join(lines)
