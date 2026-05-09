"""ToolSearch — deferred tool discovery via keyword or direct selection."""

from __future__ import annotations

import json
import re

from .compat import function_tool

# --- Deferred tool registry ---

_deferred_tools: list | None = None


def _set_deferred_tools(tools: list | None) -> None:
    global _deferred_tools
    _deferred_tools = tools


def _get_deferred_tools() -> list:
    return _deferred_tools or []


# --- Scoring ---


def _parse_tool_name(name: str) -> list[str]:
    """Split tool name into searchable parts."""
    if "__" in name:
        return [p.lower() for segment in name.split("__") for p in segment.split("_") if p]
    return [p.lower() for p in name.split("_") if p]


def _score_tool(tool, terms: list[str], required: set[str]) -> int:
    """Score a tool against search terms. Higher = better match."""
    name = getattr(tool, "name", "")
    desc = getattr(tool, "description", "")
    parts = _parse_tool_name(name)
    name_lower = name.lower()
    desc_lower = desc.lower()

    for req in required:
        found = req in name_lower or req in desc_lower or any(req in p for p in parts)
        if not found:
            return -1

    score = 0
    is_mcp = "__" in name

    for term in terms:
        term_lower = term.lstrip("+").lower()

        if term_lower in parts:
            score += 12 if is_mcp else 10
        elif any(term_lower in p for p in parts):
            score += 6 if is_mcp else 5
        elif re.search(rf"\b{re.escape(term_lower)}\b", desc_lower):
            score += 2
        elif term_lower in name_lower:
            score += 3

    return score


# --- Plain implementation ---


def tool_search(query: str, max_results: int = 5) -> str:
    """Search for available tools by keyword or direct selection."""
    tools = _get_deferred_tools()

    # Direct selection mode
    if query.startswith("select:"):
        requested = {n.strip().lower() for n in query[7:].split(",") if n.strip()}
        matches = [t for t in tools if getattr(t, "name", "").lower() in requested]
        return json.dumps(
            {
                "matches": [getattr(t, "name", "") for t in matches],
                "query": query,
                "total_deferred_tools": len(tools),
            }
        )

    # Keyword search mode
    raw_terms = query.split()
    required = {t.lstrip("+").lower() for t in raw_terms if t.startswith("+")}
    terms = [t for t in raw_terms if t]

    scored: list[tuple[int, str]] = []
    for tool in tools:
        s = _score_tool(tool, terms, required)
        if s > 0:
            scored.append((s, getattr(tool, "name", "")))

    scored.sort(key=lambda x: -x[0])
    matches = [name for _, name in scored[:max_results]]

    return json.dumps(
        {
            "matches": matches,
            "query": query,
            "total_deferred_tools": len(tools),
        }
    )


# --- @function_tool wrapper ---

tool_search_tool = function_tool(tool_search)
