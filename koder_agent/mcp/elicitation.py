"""MCP elicitation support -- interactive form/URL prompts from MCP servers."""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import (
    ElicitRequestParams,
    ElicitResult,
)
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, FloatPrompt, IntPrompt, Prompt
from rich.text import Text

logger = logging.getLogger(__name__)

# Sentinel used by ClientSession.initialize() to detect custom callbacks.
# We import the default so we can avoid identity-checking issues.


class ElicitationHandler:
    """Handles MCP elicitation requests via Rich console prompts.

    Two modes are supported:

    * **form** -- the server sends a JSON Schema describing fields.
      Each field is rendered as a Rich console prompt (text, integer,
      float, boolean, or enum selection).

    The handler also dispatches a ``"Elicitation"`` hook event so that
    hooks can auto-respond without user interaction (useful for
    headless/CI scenarios).
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    # ------------------------------------------------------------------
    # Public entry point (matches ``ElicitationFnT`` protocol)
    # ------------------------------------------------------------------

    async def __call__(
        self,
        context: Any,  # RequestContext[ClientSession, Any]
        params: ElicitRequestParams,
    ) -> ElicitResult:
        """Dispatch an elicitation request from an MCP server."""

        # Try hook-based auto-response first.
        hook_result = self._try_hook_auto_response(params)
        if hook_result is not None:
            return hook_result

        return self._handle_form(params)

    # ------------------------------------------------------------------
    # Form mode
    # ------------------------------------------------------------------

    def _handle_form(self, params: ElicitRequestParams) -> ElicitResult:
        """Present JSON-Schema-defined fields as Rich console prompts."""

        schema = params.requestedSchema or {}
        properties: dict[str, Any] = schema.get("properties", {})
        required_fields: list[str] = schema.get("required", [])

        if not properties:
            self._console.print(
                Panel(params.message, title="MCP Server Request", border_style="cyan")
            )
            accept = Confirm.ask("Accept?", console=self._console, default=True)
            return ElicitResult(
                action="accept" if accept else "decline",
                content=None,
            )

        # Display the server's message
        self._console.print()
        self._console.print(Panel(params.message, title="MCP Server Request", border_style="cyan"))

        content: dict[str, str | int | float | bool | list[str] | None] = {}

        for field_name, field_schema in properties.items():
            is_required = field_name in required_fields
            value = self._prompt_field(field_name, field_schema, required=is_required)
            if value is not None:
                content[field_name] = value

        # Confirm submission
        self._console.print()
        accept = Confirm.ask("Submit this response?", console=self._console, default=True)
        if not accept:
            return ElicitResult(action="decline", content=None)

        return ElicitResult(action="accept", content=content)

    def _prompt_field(
        self,
        name: str,
        schema: dict[str, Any],
        *,
        required: bool = False,
    ) -> str | int | float | bool | list[str] | None:
        """Prompt for a single field based on its JSON Schema type."""

        field_type = schema.get("type", "string")
        description = schema.get("description", "")
        default = schema.get("default")
        enum_values: list[str] | None = schema.get("enum")

        # Build the prompt label
        label = Text(name, style="bold")
        if description:
            label.append(f" ({description})", style="dim")
        if required:
            label.append(" *", style="red")

        label_str = str(label)

        # Enum selection
        if enum_values:
            self._console.print(f"  {label_str}")
            for i, val in enumerate(enum_values, 1):
                self._console.print(f"    [{i}] {val}")
            choice = Prompt.ask(
                "  Choose",
                console=self._console,
                default=str(default) if default else None,
            )
            # Accept numeric index or literal value
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(enum_values):
                    return enum_values[idx]
            except (ValueError, TypeError):
                pass
            if choice in enum_values:
                return choice
            # Fall back to first if required, else None
            return enum_values[0] if required else None

        # Boolean
        if field_type == "boolean":
            return Confirm.ask(
                f"  {label_str}",
                console=self._console,
                default=bool(default) if default is not None else False,
            )

        # Integer
        if field_type == "integer":
            try:
                return IntPrompt.ask(
                    f"  {label_str}",
                    console=self._console,
                    default=int(default) if default is not None else None,
                )
            except KeyboardInterrupt:
                return int(default) if default is not None else None

        # Number / float
        if field_type == "number":
            try:
                return FloatPrompt.ask(
                    f"  {label_str}",
                    console=self._console,
                    default=float(default) if default is not None else None,
                )
            except KeyboardInterrupt:
                return float(default) if default is not None else None

        # Array of strings — ElicitResult.content only accepts scalar values,
        # so we join the items back into a comma-separated string.
        if field_type == "array":
            self._console.print(f"  {label_str} (comma-separated values)")
            raw = Prompt.ask(
                "  Values",
                console=self._console,
                default=",".join(default) if isinstance(default, list) else "",
            )
            if raw:
                return ", ".join(v.strip() for v in raw.split(",") if v.strip())
            return "" if required else None

        # Default: string
        raw = Prompt.ask(
            f"  {label_str}",
            console=self._console,
            default=str(default) if default is not None else ("" if required else None),
        )
        return raw if raw else None

    # ------------------------------------------------------------------
    # Hook auto-response
    # ------------------------------------------------------------------

    def _try_hook_auto_response(
        self,
        params: ElicitRequestParams,
    ) -> ElicitResult | None:
        """Dispatch ``Elicitation`` hook; return an auto-response if one fires."""

        try:
            import os

            from koder_agent.harness.hooks.runtime import dispatch_command_hooks

            payload: dict[str, Any] = {
                "event": "Elicitation",
                "message": params.message,
                "mode": "form",
                "requestedSchema": params.requestedSchema,
            }

            result = dispatch_command_hooks(
                cwd=os.getcwd(),
                event_name="Elicitation",
                payload=payload,
            )

            if result.elicitation_action in ("accept", "decline", "cancel"):
                return ElicitResult(
                    action=result.elicitation_action,
                    content=result.elicitation_content,
                )
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("Elicitation hook dispatch failed: %s", exc)

        return None


# ------------------------------------------------------------------
# Module-level singleton and factory
# ------------------------------------------------------------------

_handler: ElicitationHandler | None = None


def get_elicitation_handler(console: Console | None = None) -> ElicitationHandler:
    """Return the module-level ``ElicitationHandler`` singleton."""
    global _handler
    if _handler is None:
        _handler = ElicitationHandler(console=console)
    return _handler
