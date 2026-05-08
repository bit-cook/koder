"""Multi-source permission rule hierarchy.

Rules come from multiple sources with defined priority.
Higher-priority sources can override lower ones.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

# Sources ordered from highest to lowest priority
SOURCE_PRIORITY = [
    "policy",  # Enterprise managed settings (highest)
    "project",  # .koder/settings.json (committed)
    "local",  # .koder/settings.local.json (gitignored)
    "user",  # ~/.koder/settings.json (global)
    "cli",  # CLI arguments
    "command",  # Slash command frontmatter
    "session",  # In-memory session rules (lowest)
]

# Regex for parsing ToolName(ruleContent) format
_TOOL_RULE_RE = re.compile(r"^(\w+)\((.+)\)$")


@dataclass
class RuleEntry:
    """A single permission rule with source tracking."""

    tool_name: str
    behavior: str  # "allow", "deny"
    content: str
    source: str


class RuleHierarchy:
    """Manages permission rules from multiple sources with priority ordering."""

    def __init__(self):
        self._rules: list[RuleEntry] = []

    def add_rule(self, tool_name: str, behavior: str, content: str, *, source: str) -> None:
        """Add a rule from a specific source."""
        if source not in SOURCE_PRIORITY:
            raise ValueError(f"Unknown source: {source}. Must be one of {SOURCE_PRIORITY}")
        # Remove duplicate if exists
        self._rules = [
            r
            for r in self._rules
            if not (
                r.tool_name == tool_name
                and r.behavior == behavior
                and r.content == content
                and r.source == source
            )
        ]
        self._rules.append(
            RuleEntry(
                tool_name=tool_name,
                behavior=behavior,
                content=content,
                source=source,
            )
        )

    def remove_rule(self, tool_name: str, behavior: str, content: str, *, source: str) -> None:
        """Remove a rule from a specific source."""
        self._rules = [
            r
            for r in self._rules
            if not (
                r.tool_name == tool_name
                and r.behavior == behavior
                and r.content == content
                and r.source == source
            )
        ]

    def load_from_settings(self, settings: dict, *, source: str) -> None:
        """Load rules from a settings dict (e.g., from settings.json).

        Expected format:
        {
            "permissions": {
                "allow": ["ToolName(ruleContent)", ...],
                "deny": ["ToolName(ruleContent)", ...],
            }
        }
        """
        perms = settings.get("permissions", {})
        for behavior in ("allow", "deny"):
            for rule_str in perms.get(behavior, []):
                match = _TOOL_RULE_RE.match(rule_str)
                if match:
                    tool_name, content = match.groups()
                    self.add_rule(tool_name, behavior, content, source=source)

    def get_effective_rules(self) -> dict[str, dict[str, list[str]]]:
        """Compute effective rules by merging all sources.

        For deny rules: a deny at ANY priority level takes effect.
        For allow rules: merged from all sources (deduplicated).
        If the same content appears as both allow and deny, deny wins
        if its source has equal or higher priority.
        """
        result: dict[str, dict[str, list[str]]] = {}

        # Group by tool+content, track highest-priority deny
        deny_set: set[tuple[str, str]] = set()
        for rule in self._rules:
            if rule.behavior == "deny":
                deny_set.add((rule.tool_name, rule.content))

        for rule in self._rules:
            tool_rules = result.setdefault(rule.tool_name, {})
            bucket = tool_rules.setdefault(rule.behavior, [])

            # Skip allow if there's a deny for same tool+content
            if rule.behavior == "allow" and (rule.tool_name, rule.content) in deny_set:
                continue

            if rule.content not in bucket:
                bucket.append(rule.content)

        return result

    def get_rules_for_tool(self, tool_name: str) -> dict[str, list[str]]:
        """Get effective rules for a specific tool."""
        all_rules = self.get_effective_rules()
        return all_rules.get(tool_name, {})

    def export_rules(self) -> dict[str, dict[str, list[str]]]:
        """Export effective rules as a deep copy."""
        return deepcopy(self.get_effective_rules())
