"""Queued interactive input captured while an agent turn is running."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

QUEUE_VISIBLE_PREFIX = "queued: "
QUEUED_TOOL_OUTPUT_HEADER = "Queued user input"


class QueuedInputManager:
    """Store prompts submitted while a model response is still in flight."""

    def __init__(self) -> None:
        self._items: list[str] = []
        self._callbacks: list[Callable[[], None]] = []

    def enqueue(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        self._items.append(clean)
        self._notify_changed()

    def drain_for_tool_result(self) -> list[str]:
        if not self._items:
            return []
        items = list(self._items)
        self._items.clear()
        self._notify_changed()
        return items

    def visible_lines(self) -> list[str]:
        return [f"{QUEUE_VISIBLE_PREFIX}{item}" for item in self._items]

    def has_pending(self) -> bool:
        return bool(self._items)

    def on_change(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def remove() -> None:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

        return remove

    def _notify_changed(self) -> None:
        for callback in list(self._callbacks):
            callback()


def append_queued_input_to_tool_output(output: Any, queued_inputs: list[str]) -> str:
    """Append queued user prompts to the model-visible tool output."""
    base = "" if output is None else str(output)
    if not queued_inputs:
        return base

    instructions = [
        f"[{QUEUED_TOOL_OUTPUT_HEADER}]",
        "The user submitted the following input while this tool was running. "
        "Treat it as additional user instruction now that the tool result is available.",
    ]
    instructions.extend(f"{index}. {item}" for index, item in enumerate(queued_inputs, start=1))
    block = "\n".join(instructions)
    if base.strip():
        return f"{base}\n\n{block}"
    return block


def strip_queued_input_from_tool_output(output: Any) -> str:
    """Remove queued prompt metadata from user-facing tool output rendering."""
    text = "" if output is None else str(output)
    marker = f"\n\n[{QUEUED_TOOL_OUTPUT_HEADER}]"
    if marker in text:
        return text.split(marker, 1)[0]
    if text.startswith(f"[{QUEUED_TOOL_OUTPUT_HEADER}]"):
        return ""
    return text


def wrap_function_tool_for_queued_input(tool: Any, manager: QueuedInputManager) -> Any:
    """Wrap a FunctionTool invocation so queued prompts ride on the next tool result."""
    holder = getattr(tool, "_koder_queued_input_holder", None)
    if isinstance(holder, dict):
        holder["manager"] = manager
        return tool

    original = getattr(tool, "on_invoke_tool", None)
    if original is None:
        return tool

    holder = {"manager": manager}

    async def _invoke_with_queued_input(context, input_json):
        result = original(context, input_json)
        if inspect.isawaitable(result):
            result = await result
        queued_manager = holder["manager"]
        queued = queued_manager.drain_for_tool_result()
        if queued:
            return append_queued_input_to_tool_output(result, queued)
        return result

    tool.on_invoke_tool = _invoke_with_queued_input
    setattr(tool, "_koder_queued_input_wrapped", True)
    setattr(tool, "_koder_queued_input_holder", holder)
    return tool
