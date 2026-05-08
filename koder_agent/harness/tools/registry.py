"""Tool registry for the harness runtime."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from koder_agent.harness.hooks.runtime import dispatch_command_hooks
from koder_agent.harness.permissions.results import PermissionEvaluationResult

ToolInvoke = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

CORE_TOOL_GROUPS: dict[str, set[str]] = {
    "code": {"code_intelligence"},
    "file": {"read_file", "write_file", "edit_file"},
    "search": {"glob_search", "grep_search"},
    "web": {"web_fetch", "web_search"},
    "mcp": {"list_mcp_resources", "read_mcp_resource", "tool_search"},
}

GROUP_MODULES = {
    "code": "code_intelligence_ops",
    "file": "file_ops",
    "search": "search_ops",
    "web": "web_ops",
    "mcp": "mcp_ops",
}

NAME_TO_GROUP = {
    tool_name: group_name
    for group_name, tool_names in CORE_TOOL_GROUPS.items()
    for tool_name in tool_names
}


@dataclass(frozen=True)
class ToolSpec:
    """Runtime-facing tool descriptor."""

    name: str
    enabled: bool = True
    invoke: ToolInvoke | None = None
    category: str | None = None


def _string_looks_like_error(content: Any, error_markers: tuple[str, ...]) -> bool:
    if not isinstance(content, str):
        return False
    return any(content.startswith(marker) for marker in error_markers)


def build_tool_result(
    name: str,
    content: Any,
    *,
    error_markers: tuple[str, ...] = (),
    status: str | None = None,
) -> dict[str, Any]:
    """Build a stable result envelope for runtime-owned tool transcripts."""
    resolved_status = status
    if resolved_status is None:
        resolved_status = "error" if _string_looks_like_error(content, error_markers) else "success"
    return {
        "tool": name,
        "status": resolved_status,
        "content": content,
    }


class ToolRegistry:
    """Registry of tool descriptors."""

    def __init__(self, tools: dict[str, ToolSpec] | None = None, permission_service=None):
        self._tools = tools or {}
        self._permission_service = permission_service

    @classmethod
    def empty(cls, permission_service=None) -> "ToolRegistry":
        return cls({}, permission_service=permission_service)

    @classmethod
    def with_permission_service(cls, permission_service) -> "ToolRegistry":
        return cls.empty(permission_service=permission_service)

    @classmethod
    def with_core_tools(
        cls, *, categories: set[str] | None = None, permission_service=None
    ) -> "ToolRegistry":
        """Create a registry with core tool modules registered."""
        registry = cls.empty(permission_service=permission_service)
        target_categories = categories or set(GROUP_MODULES.keys())
        for category in sorted(target_categories):
            module_name = GROUP_MODULES.get(category)
            if module_name:
                registry.register_module(module_name)
        return registry

    def register(self, spec: ToolSpec) -> None:
        if spec.invoke is not None and self._permission_service is not None:
            raw_invoke = spec.invoke

            async def guarded_invoke(
                arguments: dict[str, Any], *, _name=spec.name, _raw=raw_invoke
            ):
                decision: PermissionEvaluationResult = (
                    await self._permission_service.evaluate_tool_call_async(
                        _name,
                        arguments,
                    )
                )
                if decision.requires_approval:
                    hook_result = dispatch_command_hooks(
                        cwd=Path.cwd(),
                        event_name="PermissionRequest",
                        match_value=_name,
                        payload={
                            "event": "PermissionRequest",
                            "tool_name": _name,
                            "tool_input": arguments,
                            "reason": decision.reason,
                        },
                    )
                    dispatch_command_hooks(
                        cwd=Path.cwd(),
                        event_name="Notification",
                        match_value="permission_prompt",
                        payload={
                            "event": "Notification",
                            "notification_type": "permission_prompt",
                            "tool_name": _name,
                            "reason": decision.reason,
                        },
                    )
                    if hook_result.permission_request_result:
                        behavior = hook_result.permission_request_result.get("behavior")
                        if behavior == "allow":
                            updated = hook_result.permission_request_result.get("updatedInput")
                            next_arguments = updated if isinstance(updated, dict) else arguments
                            result = await _raw(next_arguments)
                            updates = hook_result.permission_request_result.get(
                                "updatedPermissions"
                            )
                            if isinstance(updates, list):
                                result["permission_updates"] = updates
                            return result
                        if behavior == "deny":
                            return {
                                "tool": _name,
                                "status": "error",
                                "content": hook_result.permission_request_result.get("message")
                                or decision.reason,
                                "permission": decision.to_dict(),
                            }
                    return {
                        "tool": _name,
                        "status": "approval_required",
                        "content": decision.reason,
                        "permission": decision.to_dict(),
                    }
                if not decision.allowed:
                    denied_result = dispatch_command_hooks(
                        cwd=Path.cwd(),
                        event_name="PermissionDenied",
                        match_value=_name,
                        payload={
                            "event": "PermissionDenied",
                            "tool_name": _name,
                            "tool_input": arguments,
                            "reason": decision.reason,
                        },
                    )
                    denied_response: dict[str, Any] = {
                        "tool": _name,
                        "status": "error",
                        "content": decision.reason,
                        "permission": decision.to_dict(),
                    }
                    if denied_result.retry:
                        denied_response["retry"] = True
                    return denied_response
                try:
                    result = await _raw(arguments)
                except Exception as exc:
                    failure_result = dispatch_command_hooks(
                        cwd=Path.cwd(),
                        event_name="PostToolUseFailure",
                        match_value=_name,
                        payload={
                            "event": "PostToolUseFailure",
                            "tool_name": _name,
                            "tool_input": arguments,
                            "error": str(exc),
                        },
                    )
                    if failure_result.blocked:
                        return {
                            "tool": _name,
                            "status": "error",
                            "content": failure_result.block_reason or str(exc),
                        }
                    raise
                if isinstance(result, dict) and result.get("status") == "error":
                    failure_result = dispatch_command_hooks(
                        cwd=Path.cwd(),
                        event_name="PostToolUseFailure",
                        match_value=_name,
                        payload={
                            "event": "PostToolUseFailure",
                            "tool_name": _name,
                            "tool_input": arguments,
                            "error": result.get("content"),
                        },
                    )
                    if failure_result.blocked:
                        result = {
                            **result,
                            "content": failure_result.block_reason or result.get("content"),
                        }
                else:
                    post_result = dispatch_command_hooks(
                        cwd=Path.cwd(),
                        event_name="PostToolUse",
                        match_value=_name,
                        payload={
                            "event": "PostToolUse",
                            "tool_name": _name,
                            "tool_input": arguments,
                            "result": result.get("content") if isinstance(result, dict) else result,
                        },
                    )
                    if post_result.blocked:
                        return {
                            "tool": _name,
                            "status": "error",
                            "content": post_result.block_reason or "Blocked by PostToolUse hook",
                        }
                return result

            spec = replace(spec, invoke=guarded_invoke)

        self._tools[spec.name] = spec

    def register_module(self, module_name: str) -> None:
        module = importlib.import_module(f"{__package__}.{module_name}")
        module.register_tools(self)

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)
