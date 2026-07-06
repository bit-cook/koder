"""Security-hardening regression tests for the permission service.

Covers audit findings on the PermissionService rule-matching / mode-override /
AI-classifier paths:

- Finding 5: allow/deny rules for shell tools must be evaluated PER SEGMENT so
  chaining (``echo hi; rm -rf ~``) can neither escalate an allow nor bypass a
  deny.
- Finding 6: acceptEdits must not auto-allow writes flagged as dangerous
  files/directories (``.git/``, ``.vscode/``, ``.idea/``, ``.koder/``).
- Finding 7: the AI classifier may only make a static approval stricter (deny),
  never convert it into an auto-run.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.permissions.ai_classifier import (  # noqa: E402
    AiShellClassifier,
    ClassificationResult,
    RiskLevel,
)
from koder_agent.harness.permissions.modes import PermissionMode  # noqa: E402
from koder_agent.harness.permissions.service import PermissionService  # noqa: E402

# ---------------------------------------------------------------------------
# Finding 5: per-segment allow/deny matching for shell tools.
# ---------------------------------------------------------------------------


class TestAllowRuleChainingEscalation:
    def test_echo_allow_does_not_greenlight_chained_rm(self):
        """`echo:*` allow must NOT auto-approve `echo hi; rm -rf ~/Documents`."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "echo:*")

        result = service.evaluate_tool_call("run_shell", {"command": "echo hi; rm -rf ~/Documents"})
        # The rm segment is not covered by any allow rule, so the command must
        # not be auto-run.
        assert not (result.allowed and not result.requires_approval)

    def test_wildcard_echo_allow_does_not_greenlight_chained_rm(self):
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "echo *")

        result = service.evaluate_tool_call(
            "run_shell", {"command": "echo hi && rm -rf ~/Documents"}
        )
        assert not (result.allowed and not result.requires_approval)

    def test_single_segment_allow_still_auto_approves(self):
        """Single-segment behavior is unchanged: a matching allow auto-approves."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "echo:*")

        result = service.evaluate_tool_call("run_shell", {"command": "echo hi"})
        assert result.allowed is True
        assert result.requires_approval is False
        assert result.matched_rule == "echo:*"

    def test_every_segment_allowed_auto_approves(self):
        """When every segment is individually allowed, the chain is allowed."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "echo:*")
        service.add_rule("run_shell", "allow", "ls:*")

        result = service.evaluate_tool_call("run_shell", {"command": "echo hi && ls -la"})
        assert result.allowed is True
        assert result.requires_approval is False


class TestDenyRuleChainingBypass:
    def test_rm_deny_fires_on_chained_segment(self):
        """`rm:*` deny must fire for `ls && rm -rf x`, not be bypassed."""
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "ls && rm -rf x"})
        assert result.allowed is False
        assert result.requires_approval is False
        assert "Denied by rule" in result.reason

    def test_rm_deny_fires_after_semicolon(self):
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "echo hi; rm file"})
        assert result.allowed is False
        assert "Denied by rule" in result.reason

    def test_deny_takes_precedence_over_allow(self):
        """Deny wins even when an allow rule also matches a segment."""
        service = PermissionService.default()
        service.add_rule("run_shell", "allow", "echo:*")
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "echo hi && rm x"})
        assert result.allowed is False
        assert "Denied by rule" in result.reason

    def test_single_segment_deny_unchanged(self):
        service = PermissionService.default()
        service.add_rule("run_shell", "deny", "rm:*")

        result = service.evaluate_tool_call("run_shell", {"command": "rm file"})
        assert result.allowed is False
        assert "Denied by rule" in result.reason


# ---------------------------------------------------------------------------
# Finding 6: acceptEdits must not auto-allow dangerous file/dir writes.
# ---------------------------------------------------------------------------


class TestAcceptEditsDangerousPaths:
    def test_accept_edits_does_not_auto_allow_git_hook_write(self, tmp_path: Path):
        """Writing `.git/hooks/pre-commit` under acceptEdits still needs approval."""
        (tmp_path / ".git" / "hooks").mkdir(parents=True)
        svc = PermissionService.default(mode=PermissionMode.ACCEPT_EDITS, workspace_root=tmp_path)
        result = svc.evaluate_tool_call(
            "write_file",
            {"file_path": str(tmp_path / ".git" / "hooks" / "pre-commit"), "content": "x"},
        )
        assert result.requires_approval is True
        assert not (result.allowed and not result.requires_approval)

    def test_accept_edits_does_not_auto_allow_vscode_write(self, tmp_path: Path):
        svc = PermissionService.default(mode=PermissionMode.ACCEPT_EDITS, workspace_root=tmp_path)
        result = svc.evaluate_tool_call(
            "write_file",
            {"file_path": str(tmp_path / ".vscode" / "settings.json"), "content": "{}"},
        )
        assert result.requires_approval is True

    def test_accept_edits_does_not_auto_allow_gitconfig_write(self, tmp_path: Path):
        svc = PermissionService.default(mode=PermissionMode.ACCEPT_EDITS, workspace_root=tmp_path)
        result = svc.evaluate_tool_call(
            "write_file",
            {"file_path": str(tmp_path / ".gitconfig"), "content": "x"},
        )
        assert result.requires_approval is True

    def test_accept_edits_still_auto_allows_ordinary_workspace_write(self, tmp_path: Path):
        """The common case (ordinary file) must still auto-allow under acceptEdits."""
        svc = PermissionService.default(mode=PermissionMode.ACCEPT_EDITS, workspace_root=tmp_path)
        result = svc.evaluate_tool_call(
            "write_file",
            {"file_path": str(tmp_path / "src" / "module.py"), "content": "print(1)"},
        )
        assert result.allowed is True
        assert result.requires_approval is False


# ---------------------------------------------------------------------------
# Finding 7: AI classifier may not upgrade a static approval to auto-allow.
# ---------------------------------------------------------------------------


class TestAiClassifierCannotEscalateToAllow:
    @pytest.mark.asyncio
    async def test_safe_verdict_keeps_static_approval(self):
        """Static requires_approval + AI SAFE -> still requires_approval."""
        service = PermissionService.default()
        mock_classifier = AsyncMock(spec=AiShellClassifier)
        mock_classifier.classify.return_value = ClassificationResult(
            command="some-unknown-tool",
            risk_level=RiskLevel.SAFE,
            allowed=True,
            reason="looks safe",
        )
        service._ai_classifier = mock_classifier

        result = await service.evaluate_tool_call_async(
            "run_shell", {"command": "some-unknown-tool"}
        )

        assert mock_classifier.classify.called
        assert result.requires_approval is True
        assert not (result.allowed and not result.requires_approval)

    @pytest.mark.asyncio
    async def test_ai_can_still_deny(self):
        """The cap preserves the AI classifier's ability to DENY."""
        service = PermissionService.default()
        mock_classifier = AsyncMock(spec=AiShellClassifier)
        mock_classifier.classify.return_value = ClassificationResult(
            command="some-unknown-tool --force",
            risk_level=RiskLevel.DANGEROUS,
            allowed=False,
            reason="looks dangerous",
        )
        service._ai_classifier = mock_classifier

        result = await service.evaluate_tool_call_async(
            "run_shell", {"command": "some-unknown-tool --force"}
        )
        assert result.allowed is False
        assert "denied" in result.reason.lower()
