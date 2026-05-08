"""Runtime hook support for subagent lifecycle and tool events."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import Agent, RunContextWrapper, RunHooks, Tool

from ..hooks.runtime import dispatch_command_hooks
from .definitions import AgentDefinition, load_agent_settings

if TYPE_CHECKING:
    from ..permissions.service import PermissionService

logger = logging.getLogger(__name__)


def _matching_rules(
    hook_config: dict[str, Any], event_name: str, match_value: str | None
) -> list[dict]:
    rules = hook_config.get(event_name) or []
    if not isinstance(rules, list):
        return []
    matched: list[dict] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        matcher = rule.get("matcher")
        if matcher is None:
            matched.append(rule)
            continue
        if match_value is None:
            continue
        try:
            if re.search(str(matcher), match_value):
                matched.append(rule)
        except re.error:
            if str(matcher) == match_value:
                matched.append(rule)
    return matched


def _run_command_hooks(rules: list[dict], payload: dict[str, Any], cwd: str | Path) -> None:
    payload_text = json.dumps(payload)
    for rule in rules:
        hooks = rule.get("hooks") or []
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict) or hook.get("type") != "command":
                continue
            command = hook.get("command")
            if not isinstance(command, str) or not command.strip():
                continue
            subprocess.run(
                command,
                input=payload_text,
                text=True,
                cwd=str(cwd),
                shell=True,
                check=False,
            )


def load_project_hook_config(cwd: str | Path) -> dict[str, Any]:
    """Load project hook configuration from koder settings."""

    settings = load_agent_settings(cwd)
    hooks = settings.get("hooks")
    return hooks if isinstance(hooks, dict) else {}


def dispatch_project_hook_event(
    *,
    cwd: str | Path,
    event_name: str,
    match_value: str | None,
    payload: dict[str, Any],
) -> Any:
    """Dispatch a non-subagent project hook event through the shared hook runner."""

    return dispatch_command_hooks(
        cwd=cwd,
        event_name=event_name,
        match_value=match_value,
        payload=payload,
    )


class SubagentLifecycleHooks(RunHooks):
    """Lifecycle hooks for subagent runs."""

    def __init__(
        self,
        *,
        agent_definition: AgentDefinition,
        cwd: str | Path,
        wrapped_hooks: RunHooks | None = None,
        permission_service: "PermissionService | None" = None,
    ):
        self.agent_definition = agent_definition
        self.cwd = Path(cwd)
        self.wrapped_hooks = wrapped_hooks
        self._permission_service = permission_service
        self.project_hooks = load_project_hook_config(self.cwd)
        self.frontmatter_hooks = agent_definition.hooks or {}

    async def on_agent_start(self, context: RunContextWrapper, agent: Agent) -> None:
        if self.wrapped_hooks:
            await self.wrapped_hooks.on_agent_start(context, agent)
        payload = {"event": "SubagentStart", "agent_type": self.agent_definition.agent_type}
        rules = _matching_rules(
            self.project_hooks, "SubagentStart", self.agent_definition.agent_type
        )
        _run_command_hooks(rules, payload, self.cwd)

    async def on_agent_end(self, context: RunContextWrapper, agent: Agent, output: Any) -> None:
        if self.wrapped_hooks:
            await self.wrapped_hooks.on_agent_end(context, agent, output)
        payload = {
            "event": "SubagentStop",
            "agent_type": self.agent_definition.agent_type,
            "output": str(output),
            "stop_hook_active": getattr(self, "_stop_hook_active", False),
        }
        project_rules = _matching_rules(
            self.project_hooks, "SubagentStop", self.agent_definition.agent_type
        )
        _run_command_hooks(project_rules, payload, self.cwd)
        stop_rules = _matching_rules(
            self.frontmatter_hooks, "Stop", self.agent_definition.agent_type
        )
        _run_command_hooks(stop_rules, payload, self.cwd)

    async def on_tool_start(self, context: RunContextWrapper, agent: Agent, tool: Tool) -> None:
        # Permission check before forwarding
        if self._permission_service is not None:
            result = self._permission_service.evaluate_tool_call(
                tool_name=tool.name,
                arguments={},
            )
            if not result.allowed and not result.requires_approval:
                logger.warning("Permission denied for tool %s: %s", tool.name, result.reason)
                raise PermissionError(f"Permission denied for {tool.name}: {result.reason}")
            if result.requires_approval:
                logger.info("Tool %s requires approval: %s", tool.name, result.reason)

        if self.wrapped_hooks:
            await self.wrapped_hooks.on_tool_start(context, agent, tool)
        payload = {
            "event": "PreToolUse",
            "agent_type": self.agent_definition.agent_type,
            "tool_name": tool.name,
        }
        rules = _matching_rules(self.frontmatter_hooks, "PreToolUse", tool.name)
        _run_command_hooks(rules, payload, self.cwd)

    async def on_tool_end(
        self,
        context: RunContextWrapper,
        agent: Agent,
        tool: Tool,
        result: str,
    ) -> None:
        if self.wrapped_hooks:
            await self.wrapped_hooks.on_tool_end(context, agent, tool, result)
        payload = {
            "event": "PostToolUse",
            "agent_type": self.agent_definition.agent_type,
            "tool_name": tool.name,
            "result": str(result),
        }
        rules = _matching_rules(self.frontmatter_hooks, "PostToolUse", tool.name)
        _run_command_hooks(rules, payload, self.cwd)
