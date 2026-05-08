"""Shared helpers for reasoning display configuration."""

from __future__ import annotations

from typing import Literal

ReasoningDisplayMode = Literal["off", "summary", "full"]

VALID_REASONING_DISPLAY_MODES: tuple[ReasoningDisplayMode, ...] = (
    "off",
    "summary",
    "full",
)

_ALIASES: dict[str, ReasoningDisplayMode] = {
    "0": "off",
    "false": "off",
    "no": "off",
    "none": "off",
    "hide": "off",
    "1": "summary",
    "true": "summary",
    "yes": "summary",
    "on": "summary",
}


def normalize_reasoning_display_mode(
    value: object,
    *,
    default: ReasoningDisplayMode = "off",
) -> ReasoningDisplayMode:
    """Normalize a config/env value into a supported reasoning display mode."""

    if value is None:
        return default
    mode = str(value).strip().lower()
    if not mode:
        return default
    if mode in _ALIASES:
        return _ALIASES[mode]
    if mode in VALID_REASONING_DISPLAY_MODES:
        return mode  # type: ignore[return-value]
    return default
