"""Single-writer lock for transcript persistence."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path


class TranscriptWriterLock:
    """Process-local shared async writer lock keyed by DB path."""

    _locks: dict[Path, "TranscriptWriterLock"] = {}

    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()

    @classmethod
    def for_path(cls, path: str | Path) -> "TranscriptWriterLock":
        resolved = Path(path).resolve()
        lock = cls._locks.get(resolved)
        if lock is None:
            lock = cls(resolved)
            cls._locks[resolved] = lock
        return lock

    @asynccontextmanager
    async def acquire(self):
        await self._lock.acquire()
        try:
            yield self
        finally:
            self._lock.release()
