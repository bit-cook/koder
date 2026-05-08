"""Memory pruning helpers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PruneResult:
    """Outcome of pruning a memory collection."""

    kept: list[dict]
    removed: list[dict]


def prune_memories(memories: list[dict], *, max_age_days: int) -> PruneResult:
    """Drop memory entries whose `age_days` exceeds the allowed maximum."""
    kept = [memory for memory in memories if memory.get("age_days", 0) <= max_age_days]
    removed = [memory for memory in memories if memory.get("age_days", 0) > max_age_days]
    return PruneResult(kept=kept, removed=removed)
