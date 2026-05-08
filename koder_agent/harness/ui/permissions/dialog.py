"""Permission dialog renderer."""

from __future__ import annotations

from koder_agent.harness.permissions.results import PermissionEvaluationResult


class PermissionDialog:
    """Render approval requests into a simple structured frame."""

    def render(self, result: PermissionEvaluationResult) -> dict:
        return {
            "title": "Permission Required" if result.requires_approval else "Permission Decision",
            "tool": result.tool_name,
            "reason": result.reason,
            "options": ["Approve once", "Always allow", "Deny"],
        }
