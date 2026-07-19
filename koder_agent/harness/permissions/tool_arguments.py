"""Canonical argument handling for permission-sensitive tool targets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ToolArgumentError(ValueError):
    """Raised when target aliases are ambiguous or invalid."""


@dataclass(frozen=True)
class ToolTargetSpec:
    """The single target field a tool authorizes and executes."""

    field: str
    aliases: tuple[str, ...] = ()


_TARGET_SPECS: dict[str, ToolTargetSpec] = {
    "run_shell": ToolTargetSpec("command"),
    "run_powershell": ToolTargetSpec("command"),
    "git_command": ToolTargetSpec("command"),
    "read_file": ToolTargetSpec("path", ("file_path",)),
    "write_file": ToolTargetSpec("path", ("file_path",)),
    "edit_file": ToolTargetSpec("path", ("file_path",)),
    "append_file": ToolTargetSpec("path", ("file_path",)),
    # notebook_edit intentionally has no compatibility aliases. Its FunctionTool
    # and implementation both declare notebook_path, so path/file_path are invalid.
    "notebook_edit": ToolTargetSpec("notebook_path"),
    "web_fetch": ToolTargetSpec("url"),
    "read_mcp_resource": ToolTargetSpec("uri"),
}

_PATH_TARGET_FIELDS = frozenset({"path", "file_path", "notebook_path"})


def canonical_target_field(tool_name: str) -> str | None:
    """Return the one target field used by ``tool_name``."""
    spec = _TARGET_SPECS.get(tool_name)
    return spec.field if spec is not None else None


def normalize_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return arguments with an unambiguous canonical target field.

    Legacy aliases are accepted only for tools that explicitly declare them. If
    both the canonical field and an alias are supplied, every value must be an
    exact string match. Tool-specific path fields belonging to another tool are
    rejected instead of being ignored or winning by key order.
    """
    if not isinstance(arguments, dict):
        raise ToolArgumentError("tool arguments must be a JSON object")

    normalized = dict(arguments)
    spec = _TARGET_SPECS.get(tool_name)
    if spec is None:
        return normalized

    allowed_target_fields = {spec.field, *spec.aliases}
    if spec.field in _PATH_TARGET_FIELDS:
        unexpected = sorted((_PATH_TARGET_FIELDS & normalized.keys()) - allowed_target_fields)
        if unexpected:
            joined = ", ".join(unexpected)
            raise ToolArgumentError(
                f"unexpected path field(s) for {tool_name}: {joined}; use {spec.field}"
            )

    supplied: list[tuple[str, Any]] = [
        (field, normalized[field]) for field in (spec.field, *spec.aliases) if field in normalized
    ]
    if not supplied:
        return normalized

    for field, value in supplied:
        if not isinstance(value, str):
            raise ToolArgumentError(f"{field} must be a string")

    canonical_value = supplied[0][1]
    if any(value != canonical_value for _field, value in supplied):
        fields = ", ".join(field for field, _value in supplied)
        raise ToolArgumentError(
            f"conflicting target fields for {tool_name}: {fields} must be exactly equal"
        )

    normalized[spec.field] = canonical_value
    for alias in spec.aliases:
        normalized.pop(alias, None)
    return normalized


def extract_canonical_tool_target(tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Extract the normalized target authorized for a tool call."""
    if tool_name in {"Skill", "skill"}:
        skill = arguments.get("skill")
        if not isinstance(skill, str):
            return None
        arguments_text = arguments.get("arguments")
        if isinstance(arguments_text, str) and arguments_text.strip():
            return f"{skill} {arguments_text.strip()}"
        return skill

    spec = _TARGET_SPECS.get(tool_name)
    if spec is None:
        return None
    value = arguments.get(spec.field)
    return value if isinstance(value, str) else None


def permission_arguments_for_target(tool_name: str, target: str) -> dict[str, str]:
    """Build canonical arguments for ``/permissions check`` and similar callers."""
    field = canonical_target_field(tool_name)
    return {field or "target": target}
