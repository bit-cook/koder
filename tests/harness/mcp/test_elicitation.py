"""Tests for MCP elicitation support."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from mcp.types import (
    ElicitRequestFormParams,
    ElicitResult,
)
from rich.console import Console

from koder_agent.mcp.elicitation import ElicitationHandler, get_elicitation_handler


@pytest.fixture
def handler():
    """Create a handler with a mock console."""
    console = Console(force_terminal=True, file=MagicMock())
    return ElicitationHandler(console=console)


# ------------------------------------------------------------------
# Form mode tests
# ------------------------------------------------------------------


class TestFormMode:
    def _make_form_params(self, message, schema=None):
        return ElicitRequestFormParams(
            message=message,
            requestedSchema=schema or {},
        )

    def test_empty_schema_accept(self, handler):
        """Empty schema shows message and asks for accept/decline."""
        params = self._make_form_params("Do you agree?")

        with patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm:
            mock_confirm.ask.return_value = True
            result = handler._handle_form(params)

        assert isinstance(result, ElicitResult)
        assert result.action == "accept"
        assert result.content is None

    def test_empty_schema_decline(self, handler):
        """Declining an empty-schema form returns 'decline'."""
        params = self._make_form_params("Do you agree?")

        with patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm:
            mock_confirm.ask.return_value = False
            result = handler._handle_form(params)

        assert result.action == "decline"

    def test_string_field(self, handler):
        """String field is prompted and returned."""
        params = self._make_form_params(
            "Enter details",
            {
                "properties": {"name": {"type": "string", "description": "Your name"}},
                "required": ["name"],
            },
        )

        with (
            patch("koder_agent.mcp.elicitation.Prompt") as mock_prompt,
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            mock_prompt.ask.return_value = "Alice"
            mock_confirm.ask.return_value = True
            result = handler._handle_form(params)

        assert result.action == "accept"
        assert result.content == {"name": "Alice"}

    def test_boolean_field(self, handler):
        """Boolean field uses Confirm prompt."""
        params = self._make_form_params(
            "Settings",
            {
                "properties": {"enabled": {"type": "boolean"}},
                "required": [],
            },
        )

        with (
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            # First call for the boolean field, second call for submission confirm
            mock_confirm.ask.side_effect = [True, True]
            result = handler._handle_form(params)

        assert result.action == "accept"
        assert result.content == {"enabled": True}

    def test_integer_field(self, handler):
        """Integer field uses IntPrompt."""
        params = self._make_form_params(
            "Count",
            {
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
        )

        with (
            patch("koder_agent.mcp.elicitation.IntPrompt") as mock_int,
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            mock_int.ask.return_value = 42
            mock_confirm.ask.return_value = True
            result = handler._handle_form(params)

        assert result.action == "accept"
        assert result.content == {"count": 42}

    def test_number_field(self, handler):
        """Number/float field uses FloatPrompt."""
        params = self._make_form_params(
            "Rate",
            {
                "properties": {"rate": {"type": "number"}},
                "required": [],
            },
        )

        with (
            patch("koder_agent.mcp.elicitation.FloatPrompt") as mock_float,
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            mock_float.ask.return_value = 3.14
            mock_confirm.ask.return_value = True
            result = handler._handle_form(params)

        assert result.action == "accept"
        assert result.content == {"rate": 3.14}

    def test_enum_field_by_index(self, handler):
        """Enum field accepts numeric index."""
        params = self._make_form_params(
            "Choose",
            {
                "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
                "required": [],
            },
        )

        with (
            patch("koder_agent.mcp.elicitation.Prompt") as mock_prompt,
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            mock_prompt.ask.return_value = "2"  # green (index 2)
            mock_confirm.ask.return_value = True
            result = handler._handle_form(params)

        assert result.action == "accept"
        assert result.content == {"color": "green"}

    def test_enum_field_by_value(self, handler):
        """Enum field accepts literal value."""
        params = self._make_form_params(
            "Choose",
            {
                "properties": {"color": {"type": "string", "enum": ["red", "green", "blue"]}},
                "required": [],
            },
        )

        with (
            patch("koder_agent.mcp.elicitation.Prompt") as mock_prompt,
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            mock_prompt.ask.return_value = "blue"
            mock_confirm.ask.return_value = True
            result = handler._handle_form(params)

        assert result.action == "accept"
        assert result.content == {"color": "blue"}

    def test_array_field(self, handler):
        """Array field joins comma-separated input into a string."""
        params = self._make_form_params(
            "Tags",
            {
                "properties": {"tags": {"type": "array"}},
                "required": [],
            },
        )

        with (
            patch("koder_agent.mcp.elicitation.Prompt") as mock_prompt,
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            mock_prompt.ask.return_value = "foo, bar, baz"
            mock_confirm.ask.return_value = True
            result = handler._handle_form(params)

        assert result.action == "accept"
        assert result.content == {"tags": "foo, bar, baz"}

    def test_decline_submission(self, handler):
        """Declining at the submission step returns 'decline'."""
        params = self._make_form_params(
            "Enter name",
            {
                "properties": {"name": {"type": "string"}},
                "required": [],
            },
        )

        with (
            patch("koder_agent.mcp.elicitation.Prompt") as mock_prompt,
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            mock_prompt.ask.return_value = "Alice"
            mock_confirm.ask.return_value = False  # Decline submission
            result = handler._handle_form(params)

        assert result.action == "decline"
        assert result.content is None


# ------------------------------------------------------------------
# Hook auto-response tests
# ------------------------------------------------------------------


class TestHookAutoResponse:
    _HOOKS_TARGET = "koder_agent.harness.hooks.runtime.dispatch_command_hooks"

    def test_hook_accept_auto_responds(self, handler):
        """When a hook returns an elicitation_action, use it."""
        params = ElicitRequestFormParams(
            message="Choose",
            requestedSchema={
                "properties": {"name": {"type": "string"}},
            },
        )

        mock_result = MagicMock()
        mock_result.elicitation_action = "accept"
        mock_result.elicitation_content = {"name": "auto-value"}

        with patch(self._HOOKS_TARGET, return_value=mock_result):
            result = asyncio.run(handler(None, params))

        assert isinstance(result, ElicitResult)
        assert result.action == "accept"
        assert result.content == {"name": "auto-value"}

    def test_hook_decline_auto_responds(self, handler):
        """Hook can auto-decline."""
        params = ElicitRequestFormParams(
            message="Confirm action",
            requestedSchema={},
        )

        mock_result = MagicMock()
        mock_result.elicitation_action = "decline"
        mock_result.elicitation_content = None

        with patch(self._HOOKS_TARGET, return_value=mock_result):
            result = asyncio.run(handler(None, params))

        assert isinstance(result, ElicitResult)
        assert result.action == "decline"

    def test_hook_no_match_falls_through(self, handler):
        """When no hook matches, fall through to interactive prompt."""
        params = ElicitRequestFormParams(
            message="Choose",
            requestedSchema={},
        )

        mock_result = MagicMock()
        mock_result.elicitation_action = None
        mock_result.elicitation_content = None

        with (
            patch(self._HOOKS_TARGET, return_value=mock_result),
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = True
            result = asyncio.run(handler(None, params))

        assert result.action == "accept"

    def test_hook_import_error_falls_through(self, handler):
        """ImportError from hooks module is gracefully handled."""
        params = ElicitRequestFormParams(
            message="Choose",
            requestedSchema={},
        )

        with (
            patch(self._HOOKS_TARGET, side_effect=ImportError("no hooks module")),
            patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm,
        ):
            mock_confirm.ask.return_value = True
            result = asyncio.run(handler(None, params))

        assert result.action == "accept"


# ------------------------------------------------------------------
# Dispatch entry point tests
# ------------------------------------------------------------------


class TestDispatch:
    def test_dispatch_form(self, handler):
        """__call__ routes form params to _handle_form."""
        params = ElicitRequestFormParams(
            message="Form",
            requestedSchema={},
        )

        with patch.object(handler, "_try_hook_auto_response", return_value=None):
            with patch("koder_agent.mcp.elicitation.Confirm") as mock_confirm:
                mock_confirm.ask.return_value = True
                result = asyncio.run(handler(None, params))

        assert result.action == "accept"


# ------------------------------------------------------------------
# Singleton tests
# ------------------------------------------------------------------


class TestSingleton:
    def test_get_elicitation_handler_returns_same_instance(self):
        """get_elicitation_handler returns a singleton."""
        # Reset the module-level singleton
        import koder_agent.mcp.elicitation as mod

        mod._handler = None

        h1 = get_elicitation_handler()
        h2 = get_elicitation_handler()
        assert h1 is h2

        # Cleanup
        mod._handler = None
