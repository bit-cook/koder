"""Agent definitions and hooks for Koder."""

from agents import (
    Agent,
    RunContextWrapper,
    RunHooks,
    Tool,
)
from rich.console import Console
from rich.text import Text

from ..core.display_context import (
    SubagentDisplayEvent,
    SubagentDisplayIdentity,
    emit_subagent_display_event,
)

console = Console()


def _truncate_agent_name(name: str, max_len: int = 40) -> str:
    """Truncate agent name if it's too long."""
    if len(name) <= max_len:
        return name
    return name[: max_len - 3] + "..."


class ToolDisplayHooks(RunHooks):
    """RunHooks implementation to display tool input/output with rich formatting."""

    def __init__(
        self,
        streaming_mode: bool = False,
        subagent_identity: SubagentDisplayIdentity | None = None,
    ):
        self.streaming_mode = streaming_mode
        self.subagent_identity = subagent_identity
        self._subagent_started = False
        self._subagent_finished = False

    def _emit_subagent_event(
        self,
        kind: str,
        *,
        tool_name: str | None = None,
        detail: str | None = None,
    ) -> None:
        identity = self.subagent_identity
        if identity is None or self._subagent_finished:
            return
        if kind == "started":
            if self._subagent_started:
                return
            self._subagent_started = True
        elif kind in {"completed", "failed", "cancelled"}:
            self._subagent_finished = True
        emit_subagent_display_event(
            SubagentDisplayEvent(
                identity=identity,
                kind=kind,
                tool_name=tool_name,
                detail=detail,
            )
        )

    def finish(self, kind: str = "completed", detail: str | None = None) -> None:
        """Mark a parent-managed child run terminal without printing to stdout."""

        self._emit_subagent_event("started")
        self._emit_subagent_event(kind, detail=detail)

    async def on_agent_start(self, context: RunContextWrapper, agent: Agent) -> None:
        """Called before the agent is invoked. Called each time the current agent changes."""
        if self.subagent_identity is not None:
            self._emit_subagent_event("started")
            return
        if self.streaming_mode:
            return

        agent_text = Text()
        agent_text.append("● ", style="green")
        agent_text.append("Agent: ", style="bold cyan")
        agent_text.append(_truncate_agent_name(agent.name), style="cyan")
        console.print(agent_text)

    async def on_tool_start(self, context: RunContextWrapper, agent: Agent, tool: Tool) -> None:
        """Display tool execution start."""
        if self.subagent_identity is not None:
            self._emit_subagent_event("started")
            self._emit_subagent_event("tool_started", tool_name=tool.name)
            return
        # Direct display waits for on_tool_end so tool names and results stay paired.

    async def on_tool_end(
        self, context: RunContextWrapper, agent: Agent, tool: Tool, result: str
    ) -> None:
        """Display tool execution result."""
        if self.subagent_identity is not None:
            self._emit_subagent_event("tool_finished", tool_name=tool.name)
            return
        if self.streaming_mode:
            return

        tool_text = Text()
        tool_text.append("● ", style="green")
        tool_text.append(tool.name, style="bold cyan")
        console.print(tool_text)

        display_result = str(result).strip()
        if len(display_result) > 200:
            display_result = display_result[:200] + "..."

        is_error = _is_error_output(display_result)
        output_style = "red" if is_error else "dim green"

        output_text = Text()
        output_text.append("  ╰─ ", style=output_style)
        output_text.append(display_result, style=output_style)
        console.print(output_text)


def _is_error_output(output: str) -> bool:
    """Check if the output indicates an error."""
    if not output:
        return False

    error_indicators = [
        "error:",
        "Error:",
        "ERROR:",
        "failed:",
        "Failed:",
        "FAILED:",
        "exception:",
        "Exception:",
        "traceback",
        "Traceback",
        "not found",
        "Not found",
        "permission denied",
        "Permission denied",
        "No such file",
        "fatal:",
    ]

    return any(indicator in output for indicator in error_indicators)


def get_display_hooks(streaming_mode: bool = False) -> RunHooks:
    """Get the display hooks instance."""
    return ToolDisplayHooks(streaming_mode=streaming_mode)


def get_subagent_display_hooks(
    *,
    group_id: str,
    agent_id: str,
    label: str,
    parent_call_id: str | None = None,
    order: int | None = None,
) -> ToolDisplayHooks:
    """Return hooks that publish compact child progress to the parent renderer."""

    return ToolDisplayHooks(
        subagent_identity=SubagentDisplayIdentity(
            group_id=group_id,
            agent_id=agent_id,
            label=label,
            parent_call_id=parent_call_id,
            order=order,
        )
    )
