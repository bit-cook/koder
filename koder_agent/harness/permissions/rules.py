"""Permission rule parsing and matching primitives."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionRule:
    """Normalized permission rule."""

    kind: str
    value: str


def parse_permission_rule(permission_rule: str) -> PermissionRule:
    """Parse exact, legacy prefix, or wildcard rules."""
    if permission_rule.startswith("Skill(") and permission_rule.endswith(")"):
        permission_rule = permission_rule[len("Skill(") : -1].strip()
    if permission_rule.endswith(":*"):
        return PermissionRule(kind="prefix", value=permission_rule[:-2])
    if "*" in permission_rule:
        return PermissionRule(kind="wildcard", value=permission_rule)
    return PermissionRule(kind="exact", value=permission_rule)


def match_permission_rule(rule: PermissionRule, command: str) -> bool:
    """Match a shell command against a normalized permission rule."""
    if rule.kind == "exact":
        return command == rule.value
    if rule.kind == "prefix":
        return command == rule.value or command.startswith(f"{rule.value} ")
    if rule.kind == "wildcard":
        pattern = rule.value
        # Preserve legacy behavior where a trailing " *" wildcard also matches bare command.
        if pattern.endswith(" *") and fnmatch.fnmatchcase(command, pattern[:-2]):
            return True
        return fnmatch.fnmatchcase(command, pattern)
    raise ValueError(f"Unknown rule kind: {rule.kind}")
