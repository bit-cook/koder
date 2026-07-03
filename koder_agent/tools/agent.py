"""Agent tool for spawning sub-agents programmatically.

Context inheritance: When spawning agents, use the AgentService's seed_items
parameter to pass parent conversation history. The ForkContext utility in
tools/fork_agent.py can build filtered message lists from the parent session
for prompt cache sharing and context continuity.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace as _replace
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .compat import function_tool

logger = logging.getLogger(__name__)

# Fork context available via tools/fork_agent.py build_fork_context()
# Pass result via AgentService seed_items parameter for context inheritance


class AgentToolModel(BaseModel):
    description: str
    prompt: str
    subagent_type: Optional[str] = None
    model: Optional[str] = None
    run_in_background: Optional[bool] = None
    name: Optional[str] = None
    team_name: Optional[str] = None
    mode: Optional[str] = None
    isolation: Optional[str] = None
    context: Optional[str] = None


async def _agent_tool_impl(
    description: str,
    prompt: str,
    subagent_type: str | None = None,
    model: str | None = None,
    run_in_background: bool | None = None,
    name: str | None = None,
    team_name: str | None = None,
    mode: str | None = None,
    isolation: str | None = None,
    context: str | None = None,
) -> str:
    """Core implementation for the agent tool."""
    from koder_agent.harness.agents.definitions import (
        get_agent_definitions,
        resolve_agent_model,
    )
    from koder_agent.harness.agents.service import AgentService

    cwd = Path.cwd()
    definitions = get_agent_definitions(cwd=cwd)

    # Resolve agent type
    effective_type = subagent_type or "general-purpose"
    selected = next(
        (a for a in definitions.active_agents if a.agent_type == effective_type),
        None,
    )
    if selected is None:
        available = [a.agent_type for a in definitions.active_agents]
        return json.dumps(
            {
                "status": "error",
                "error": f"Unknown agent type: {effective_type}",
                "available_agents": available,
            }
        )

    # Apply model override
    if model:
        model_override = model
    else:
        model_override = resolve_agent_model(selected)

    # Apply isolation from parameter or definition
    effective_isolation = isolation or selected.isolation

    # Build agent definition with overrides
    agent_def = selected
    if model_override and model_override != selected.model:
        agent_def = _replace(agent_def, model=model_override)
    if effective_isolation and effective_isolation != selected.isolation:
        agent_def = _replace(agent_def, isolation=effective_isolation)

    service = AgentService()

    # Build fork context if requested
    seed_items = None
    if context == "fork":
        from koder_agent.core.session import EnhancedSQLiteSession
        from koder_agent.tools.fork_agent import build_fork_context

        # Try to get parent session messages
        try:
            parent_session = EnhancedSQLiteSession()
            parent_messages = await parent_session.get_items()
            if parent_messages:
                fork_ctx = build_fork_context(parent_messages)
                seed_items = fork_ctx.to_messages()
        except Exception:
            logger.debug("Failed to get parent session messages for fork context", exc_info=True)

    # Check if background tasks are disabled via env var
    bg_disabled = os.environ.get("KODER_DISABLE_BACKGROUND_TASKS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

    # background=true in agent definition forces async execution
    should_run_async = (run_in_background or (agent_def.background is True)) and not bg_disabled

    # Async (background) mode
    if should_run_async:
        record = await service.launch_background(
            agent_definition=agent_def,
            prompt=prompt,
            description=description,
            seed_items=seed_items,
            cwd=cwd,
        )
        # Register name for SendMessage routing
        if name:
            service.register_name(name, record.id)
        return json.dumps(
            {
                "status": "async_launched",
                "agent_id": record.id,
                "agent_type": agent_def.agent_type,
                "description": description,
                "output_file": record.output_path,
            }
        )

    # Sync (blocking) mode
    result = await service.run_sync(
        agent_definition=agent_def,
        prompt=prompt,
        seed_items=seed_items,
        cwd=cwd,
    )
    return json.dumps(
        {
            "status": "completed",
            "agent_type": agent_def.agent_type,
            "result": result,
        }
    )


@function_tool
async def agent_tool(
    description: str,
    prompt: str,
    subagent_type: str | None = None,
    model: str | None = None,
    run_in_background: bool | None = None,
    name: str | None = None,
    team_name: str | None = None,
    mode: str | None = None,
    isolation: str | None = None,
    context: str | None = None,
) -> str:
    """Launch a new agent to handle complex, multi-step tasks autonomously.

    Args:
        description: A short (3-5 word) description of the task.
        prompt: The task for the agent to perform.
        subagent_type: The type of specialized agent to use (e.g. 'Explore', 'Plan').
        model: Optional model override ('sonnet', 'opus', 'haiku').
        run_in_background: Set to true to run this agent in the background.
        name: Name for the spawned agent. Makes it addressable via send_message.
        team_name: Team name for spawning within a team context.
        mode: Permission mode for spawned teammate (e.g. 'plan').
        isolation: Isolation mode ('worktree' for isolated git worktree).
        context: Context inheritance mode ('fork' to share parent conversation history).
    """
    return await _agent_tool_impl(
        description=description,
        prompt=prompt,
        subagent_type=subagent_type,
        model=model,
        run_in_background=run_in_background,
        name=name,
        team_name=team_name,
        mode=mode,
        isolation=isolation,
        context=context,
    )
