"""JSON-file cron job persistence."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CronStorage:
    """Stores cron jobs in a single JSON file.

    File format:
        {"tasks": [{"id": "...", "cron": "...", "prompt": "...", ...}, ...]}
    """

    def __init__(self, path: Path, *, max_jobs: int = 50):
        self._path = path
        self._max_jobs = max_jobs

    def _read(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text())
        return data.get("tasks", [])

    def _write(self, tasks: list[dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"tasks": tasks}, indent=2))

    def create(
        self,
        *,
        cron: str,
        prompt: str,
        recurring: bool = True,
    ) -> dict[str, Any]:
        tasks = self._read()
        if len(tasks) >= self._max_jobs:
            raise ValueError(f"Job limit reached ({self._max_jobs}). Delete existing jobs first.")

        job: dict[str, Any] = {
            "id": uuid.uuid4().hex[:8],
            "cron": cron,
            "prompt": prompt,
            "recurring": recurring,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        tasks.append(job)
        self._write(tasks)
        return job

    def list_all(self) -> list[dict[str, Any]]:
        return self._read()

    def delete(self, job_id: str) -> bool:
        tasks = self._read()
        filtered = [t for t in tasks if t["id"] != job_id]
        if len(filtered) == len(tasks):
            return False
        self._write(filtered)
        return True

    def get(self, job_id: str) -> dict[str, Any] | None:
        for t in self._read():
            if t["id"] == job_id:
                return t
        return None


_default_storage: CronStorage | None = None


def default_cron_storage() -> CronStorage:
    global _default_storage
    if _default_storage is None:
        root = Path.home() / ".koder"
        _default_storage = CronStorage(root / "scheduled_tasks.json")
    return _default_storage


def set_default_cron_storage(storage: CronStorage | None) -> None:
    global _default_storage
    _default_storage = storage
