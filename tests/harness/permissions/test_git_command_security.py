"""Security regression tests for git_command permission bypass (C2).

Ensures git_command uses per-segment rule matching with command substitution
and chaining guards, matching run_shell's protections.
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


class TestGitCommandSegmentMatching:
    """git_command must use segment-aware rule matching like run_shell."""

    def test_git_command_chained_with_destructive_not_auto_allowed(self):
        """git log:* allow must NOT greenlight `git log && rm -rf ~`."""
        service = PermissionService.default()
        service.add_rule("git_command", "allow", "git log:*")

        result = service.evaluate_tool_call("git_command", {"command": "log && rm -rf ~"})
        # Must not be auto-allowed (requires approval or denied)
        assert not (result.allowed and not result.requires_approval)

    def test_git_command_with_substitution_not_auto_allowed(self):
        """git log:* allow must NOT greenlight `git log $(rm -rf ~)`."""
        service = PermissionService.default()
        service.add_rule("git_command", "allow", "git log:*")

        result = service.evaluate_tool_call("git_command", {"command": "log $(rm -rf ~)"})
        assert not (result.allowed and not result.requires_approval)

    def test_git_command_single_readonly_still_allowed(self):
        """Single read-only git command with matching rule still auto-allows."""
        service = PermissionService.default()
        service.add_rule("git_command", "allow", "git log:*")

        result = service.evaluate_tool_call("git_command", {"command": "log --oneline"})
        assert result.allowed is True
        assert result.requires_approval is False

    def test_git_command_deny_rule_blocks_chained(self):
        """Deny rule on git_command blocks matching segment in a chain."""
        service = PermissionService.default()
        service.add_rule("git_command", "deny", "git push:*")

        result = service.evaluate_tool_call("git_command", {"command": "log && push --force"})
        assert result.allowed is False
