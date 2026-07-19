"""Tool registry primitives for the harness runtime."""

import sys

from .registry import (
    DEFAULT_REPLACEMENT_HISTORY_LIMIT,
    CandidateThreadStartError,
    DuplicateToolError,
    ReentrantToolModuleRegistrationError,
    ToolRegistry,
    ToolReplacement,
    ToolSpec,
    ToolSpecSummary,
    UnmanagedToolModuleReloadError,
    UnpublishedToolModuleAccessError,
)


def __getattr__(name: str):
    """Resolve registry-published child modules from their canonical mapping."""
    qualified_name = f"{__name__}.{name}"
    try:
        return sys.modules[qualified_name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


def __dir__() -> list[str]:
    prefix = f"{__name__}."
    children = {
        qualified_name[len(prefix) :]
        for qualified_name in sys.modules
        if qualified_name.startswith(prefix) and "." not in qualified_name[len(prefix) :]
    }
    return sorted({*globals(), *children})


__all__ = [
    "CandidateThreadStartError",
    "DEFAULT_REPLACEMENT_HISTORY_LIMIT",
    "DuplicateToolError",
    "ReentrantToolModuleRegistrationError",
    "ToolRegistry",
    "ToolReplacement",
    "ToolSpec",
    "ToolSpecSummary",
    "UnmanagedToolModuleReloadError",
    "UnpublishedToolModuleAccessError",
]
