"""Typed permission evaluation results."""

from __future__ import annotations

from dataclasses import dataclass

from .modes import PermissionMode


@dataclass(frozen=True)
class PermissionEvaluationResult:
    """Outcome of evaluating a pending tool call."""

    tool_name: str
    allowed: bool
    requires_approval: bool
    reason: str
    mode: PermissionMode
    matched_rule: str | None = None
    sandbox_backend: str | None = None
    sandbox_cwd: str | None = None
    sandbox_policy_digest: str | None = None
    sandbox_capability_digest: str | None = None

    @classmethod
    def allow(
        cls,
        *,
        tool_name: str,
        mode: PermissionMode,
        reason: str = "allowed",
        matched_rule: str | None = None,
        sandbox_backend: str | None = None,
        sandbox_cwd: str | None = None,
        sandbox_policy_digest: str | None = None,
        sandbox_capability_digest: str | None = None,
    ) -> "PermissionEvaluationResult":
        return cls(
            tool_name=tool_name,
            allowed=True,
            requires_approval=False,
            reason=reason,
            mode=mode,
            matched_rule=matched_rule,
            sandbox_backend=sandbox_backend,
            sandbox_cwd=sandbox_cwd,
            sandbox_policy_digest=sandbox_policy_digest,
            sandbox_capability_digest=sandbox_capability_digest,
        )

    @classmethod
    def approval_required(
        cls,
        *,
        tool_name: str,
        reason: str,
        mode: PermissionMode = PermissionMode.DEFAULT,
        matched_rule: str | None = None,
    ) -> "PermissionEvaluationResult":
        return cls(
            tool_name=tool_name,
            allowed=False,
            requires_approval=True,
            reason=reason,
            mode=mode,
            matched_rule=matched_rule,
        )

    @classmethod
    def deny(
        cls,
        *,
        tool_name: str,
        reason: str,
        mode: PermissionMode,
        matched_rule: str | None = None,
    ) -> "PermissionEvaluationResult":
        return cls(
            tool_name=tool_name,
            allowed=False,
            requires_approval=False,
            reason=reason,
            mode=mode,
            matched_rule=matched_rule,
        )

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "tool": self.tool_name,
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "mode": self.mode.value,
            "matched_rule": self.matched_rule,
        }
