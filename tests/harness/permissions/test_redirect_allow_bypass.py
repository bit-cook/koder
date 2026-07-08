"""Security regression tests for redirect allow bypass (C3).

Ensures write redirections cannot be smuggled past allow rules.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.permissions.service import PermissionService


class TestRedirectAllowBypass:
    """Write redirections must not be auto-allowed by prefix rules."""

    def test_echo_allow_does_not_greenlight_redirect_to_file(self):
        """`echo:*` allow must NOT auto-approve `echo hi > /etc/passwd`."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "echo:*")

        result = service.evaluate_tool_call("run_shell", {"command": "echo hi > /etc/passwd"})
        assert not (result.allowed and not result.requires_approval)

    def test_echo_allow_does_not_greenlight_append_redirect(self):
        """`echo:*` allow must NOT auto-approve `echo payload >> .bashrc`."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "echo:*")

        result = service.evaluate_tool_call("run_shell", {"command": "echo payload >> .bashrc"})
        assert not (result.allowed and not result.requires_approval)

    def test_redirect_to_dev_null_still_classified_by_static(self):
        """Redirect to /dev/null is eventually allowed by static classifier."""
        service = PermissionService.default()
        # No allow rules - pure static classification
        result = service.evaluate_tool_call("run_shell", {"command": "echo hi > /dev/null"})
        # Static classifier treats > /dev/null as read-only (WRITE_REDIRECTION_PATTERN
        # exempts /dev/null via negative lookahead)
        assert result.allowed is True

    def test_deny_rule_still_catches_redirect_commands(self):
        """Deny rules fire BEFORE the redirect guard (deny checked first)."""
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "echo:*")

        result = service.evaluate_tool_call("run_shell", {"command": "echo hi > /etc/passwd"})
        assert result.allowed is False


class TestSandboxAutoAllowPolicyCheck:
    """C5: auto_allow_bash_if_sandboxed requires policy to be non-None."""

    def test_auto_allow_requires_policy_not_none(self):
        """When sandbox policy is None, auto_allow must not fire."""
        from unittest.mock import MagicMock, patch

        service = PermissionService.default()

        # Create a mock sandbox state: enabled, backend available, but NO policy
        mock_state = MagicMock()
        mock_state.enabled = True
        mock_state.backend = "unix-local"
        mock_state.backend_available = True
        mock_state.backend_reason = ""
        mock_state.platform_enabled = True
        mock_state.auto_allow_bash_if_sandboxed = True
        mock_state.policy = None  # The edge case

        with (
            patch(
                "koder_agent.harness.permissions.service.resolve_sandbox_settings",
                return_value=mock_state,
            ),
            patch(
                "koder_agent.harness.permissions.service.is_excluded_command",
                return_value=False,
            ),
        ):
            result = service.evaluate_tool_call("run_shell", {"command": "rm -rf /tmp/test"})
        # Must NOT be auto-allowed (should require approval)
        assert result.requires_approval is True

    def test_auto_allow_with_valid_policy_works(self):
        """When policy exists and backend available, auto_allow fires."""
        from unittest.mock import MagicMock, patch

        service = PermissionService.default()

        mock_state = MagicMock()
        mock_state.enabled = True
        mock_state.backend = "unix-local"
        mock_state.backend_available = True
        mock_state.backend_reason = ""
        mock_state.platform_enabled = True
        mock_state.auto_allow_bash_if_sandboxed = True
        mock_state.policy = MagicMock()  # Valid policy object

        # Mock the policy violation checks to return None (no violation)
        with (
            patch(
                "koder_agent.harness.permissions.service.resolve_sandbox_settings",
                return_value=mock_state,
            ),
            patch(
                "koder_agent.harness.permissions.service.is_excluded_command",
                return_value=False,
            ),
            patch(
                "koder_agent.harness.permissions.service.read_only_violation",
                return_value=None,
            ),
            patch(
                "koder_agent.harness.permissions.service.protected_write_violation",
                return_value=None,
            ),
        ):
            result = service.evaluate_tool_call("run_shell", {"command": "rm -rf /tmp/test"})
        # Should be auto-allowed
        assert result.allowed is True
        assert result.requires_approval is False
