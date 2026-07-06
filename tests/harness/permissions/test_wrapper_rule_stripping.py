"""Tests for allow/deny rule matching against wrapper-stripped shell targets.

Covers the audit finding ``allow-deny-envvar-safewrapper-stripping``: a prefix
allow rule such as ``npm test:*`` must generalize across environment-variable
prefixes (``FOO=bar npm test``) and safe command-runner wrappers
(``env npm test``, ``timeout 5 npm test``). Rule matching normalizes each shell
segment down to its effective inner command (reusing the Wave-1 runner
resolver) and matches rules against BOTH the raw and normalized forms.

Critically, this generalization only makes ALLOW matching more permissive; it
must NOT weaken DENY matching. A wrapper / env prefix can never smuggle its
inner command past a deny rule (``env rm``/``FOO=1 rm`` still hit an ``rm``
deny), and the Wave-1 per-segment allow(every)/deny(any) discipline stays
intact.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues.
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.permissions.service import PermissionService  # noqa: E402
from koder_agent.harness.permissions.shell_classifier import (  # noqa: E402
    normalize_segment_for_rule,
)


def _auto_allowed(result) -> bool:
    return result.allowed and not result.requires_approval


def _hard_denied(result) -> bool:
    return (not result.allowed) and (not result.requires_approval)


# --------------------------------------------------------------------------- #
# normalize_segment_for_rule (unit)                                           #
# --------------------------------------------------------------------------- #


class TestNormalizeSegmentForRule:
    def test_strips_leading_env_assignment(self):
        assert normalize_segment_for_rule(["FOO=bar", "npm", "test"]) == "npm test"

    def test_strips_multiple_leading_assignments(self):
        assert normalize_segment_for_rule(["FOO=1", "BAR=2", "npm", "test"]) == "npm test"

    def test_strips_env_wrapper(self):
        assert normalize_segment_for_rule(["env", "npm", "test", "--watch"]) == "npm test --watch"

    def test_strips_env_wrapper_with_assignment(self):
        assert normalize_segment_for_rule(["env", "FOO=bar", "npm", "test"]) == "npm test"

    def test_strips_timeout_wrapper_and_duration(self):
        assert normalize_segment_for_rule(["timeout", "5", "npm", "test"]) == "npm test"

    def test_returns_none_when_nothing_stripped(self):
        # Plain command with no assignment/wrapper: raw already equals normalized.
        assert normalize_segment_for_rule(["npm", "test"]) is None

    def test_returns_none_for_empty(self):
        assert normalize_segment_for_rule([]) is None

    def test_returns_none_for_assignment_only(self):
        assert normalize_segment_for_rule(["FOO=bar"]) is None

    def test_returns_none_for_bare_wrapper(self):
        # Bare ``env`` wraps nothing concrete.
        assert normalize_segment_for_rule(["env"]) is None


# --------------------------------------------------------------------------- #
# Target behavior: allow rules generalize across env prefix + safe wrappers    #
# --------------------------------------------------------------------------- #


class TestAllowRuleGeneralization:
    def test_env_assignment_prefix_auto_allowed(self):
        """``npm test:*`` allow must auto-approve ``FOO=bar npm test``."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")

        result = service.evaluate_tool_call("run_shell", {"command": "FOO=bar npm test"})
        assert _auto_allowed(result)
        assert result.matched_rule == "npm test:*"

    def test_env_wrapper_auto_allowed(self):
        """``npm test:*`` allow must auto-approve ``env npm test --watch``."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")

        result = service.evaluate_tool_call("run_shell", {"command": "env npm test --watch"})
        assert _auto_allowed(result)
        assert result.matched_rule == "npm test:*"

    def test_timeout_wrapper_auto_allowed(self):
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")

        result = service.evaluate_tool_call("run_shell", {"command": "timeout 5 npm test"})
        assert _auto_allowed(result)
        assert result.matched_rule == "npm test:*"

    def test_multiple_env_assignments_auto_allowed(self):
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")

        result = service.evaluate_tool_call("run_shell", {"command": "FOO=1 BAR=2 npm test"})
        assert _auto_allowed(result)

    def test_env_wrapper_with_assignment_auto_allowed(self):
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")

        result = service.evaluate_tool_call("run_shell", {"command": "env FOO=bar npm test"})
        assert _auto_allowed(result)

    def test_plain_command_still_auto_allowed(self):
        """Non-regression: unwrapped ``npm test`` still matches the raw form."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")

        result = service.evaluate_tool_call("run_shell", {"command": "npm test"})
        assert _auto_allowed(result)
        assert result.matched_rule == "npm test:*"

    def test_wrapped_segment_in_chain_all_allowed(self):
        """Every-segment discipline: each segment (wrapped or not) must be allowed."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")
        service.add_rule("run_shell", "allow", "ls:*")

        result = service.evaluate_tool_call("run_shell", {"command": "env npm test && ls -la"})
        assert _auto_allowed(result)


# --------------------------------------------------------------------------- #
# Security: wrappers / env prefixes must NOT smuggle a command past a deny     #
# --------------------------------------------------------------------------- #


class TestDenyNotWeakenedByWrappers:
    def test_env_wrapper_does_not_smuggle_past_rm_deny(self):
        """``rm:*`` deny must still block ``env rm -rf x``."""
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "env rm -rf x"})
        assert _hard_denied(result)
        assert result.matched_rule == "rm:*"
        assert "Denied by rule" in result.reason

    def test_env_assignment_does_not_smuggle_past_rm_deny(self):
        """``rm:*`` deny must still block ``FOO=1 rm -rf x`` (env-prefix bypass)."""
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "FOO=1 rm -rf x"})
        assert _hard_denied(result)
        assert result.matched_rule == "rm:*"

    def test_timeout_wrapper_does_not_smuggle_past_rm_deny(self):
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "timeout 3 rm x"})
        assert _hard_denied(result)

    def test_env_wrapper_with_assignment_does_not_smuggle_past_rm_deny(self):
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "env FOO=1 rm x"})
        assert _hard_denied(result)

    def test_plain_rm_still_denied(self):
        """Non-regression: bare ``rm file`` still hits the deny rule."""
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "rm file"})
        assert _hard_denied(result)

    def test_deny_takes_precedence_over_allow_through_wrapper(self):
        """Deny wins even when the same wrapped command also has an allow rule."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "rm:*")
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "env rm x"})
        assert _hard_denied(result)
        assert "Denied by rule" in result.reason

    def test_deny_on_wrapped_segment_in_chain(self):
        """Deny fires on a wrapped inner command anywhere in a chain."""
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "ls && env rm -rf x"})
        assert _hard_denied(result)


# --------------------------------------------------------------------------- #
# Non-regression: unrelated commands are not affected by the normalization     #
# --------------------------------------------------------------------------- #


class TestUnrelatedCommandsUnaffected:
    def test_unrelated_wrapped_command_still_requires_approval(self):
        """A wrapped command NOT covered by the allow rule still needs approval."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")

        result = service.evaluate_tool_call("run_shell", {"command": "env cp a b"})
        assert not _auto_allowed(result)
        assert result.requires_approval

    def test_readonly_command_still_auto_allowed_by_classifier(self):
        """No rule involved: read-only classification is unchanged."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")

        result = service.evaluate_tool_call("run_shell", {"command": "ls -la"})
        assert _auto_allowed(result)
        assert result.reason == "read-only command"

    def test_allow_prefix_does_not_greenlight_chained_unwrapped_rm(self):
        """Chaining discipline preserved: an uncovered ``rm`` segment blocks auto-run."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "npm test:*")

        result = service.evaluate_tool_call(
            "run_shell", {"command": "env npm test; rm -rf ~/Documents"}
        )
        assert not _auto_allowed(result)

    def test_wrapper_alone_without_rule_not_auto_allowed(self):
        """With no allow rule, a wrapped mutating command is not auto-run."""
        service = PermissionService.default()

        result = service.evaluate_tool_call("run_shell", {"command": "env touch foo.txt"})
        assert not _auto_allowed(result)
