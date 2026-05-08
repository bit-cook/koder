"""Track slash command / skill usage for recently-used sorting."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path


class SkillUsageTracker:
    """Reads/writes usage data to ``~/.koder/skill-usage.json``.

    Usage count and recency are combined with exponential decay
    (halve every 7 days) to rank recently-used commands higher.
    """

    DEBOUNCE_SECONDS = 60.0
    DECAY_HALF_LIFE_DAYS = 7.0

    def __init__(self, store_path: Path | None = None):
        self._path = store_path or (Path.home() / ".koder" / "skill-usage.json")
        self._last_write: dict[str, float] = {}

    def _load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def record(self, skill_name: str) -> None:
        """Increment usage for *skill_name* with 60-second debounce."""
        now = time.time()
        last = self._last_write.get(skill_name, 0.0)
        if now - last < self.DEBOUNCE_SECONDS:
            return
        self._last_write[skill_name] = now

        data = self._load()
        entry = data.get(skill_name, {"usage_count": 0, "last_used_at": 0})
        entry["usage_count"] = entry.get("usage_count", 0) + 1
        entry["last_used_at"] = now
        data[skill_name] = entry
        self._save(data)

    def get_score(self, skill_name: str) -> float:
        """Return a usage score with exponential recency decay."""
        data = self._load()
        entry = data.get(skill_name)
        if not entry:
            return 0.0
        count = entry.get("usage_count", 0)
        last_used = entry.get("last_used_at", 0)
        days_since = (time.time() - last_used) / 86400.0
        recency = max(0.1, math.pow(0.5, days_since / self.DECAY_HALF_LIFE_DAYS))
        return count * recency

    def sort_commands(
        self,
        commands: list[tuple[str, str]],
        *,
        top_n: int = 5,
    ) -> list[tuple[str, str]]:
        """Return *commands* sorted with top recently-used first."""
        scored = [(self.get_score(name), name, desc) for name, desc in commands]
        recently_used = sorted(
            [(s, n, d) for s, n, d in scored if s > 0],
            key=lambda t: -t[0],
        )[:top_n]
        recent_names = {n for _, n, _ in recently_used}
        rest = [(n, d) for n, d in commands if n not in recent_names]
        return [(n, d) for _, n, d in recently_used] + rest
