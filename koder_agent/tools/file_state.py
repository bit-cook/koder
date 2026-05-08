"""Track file read state for read-before-write enforcement."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class _FileReadRecord:
    timestamp: float  # os.stat mtime at read time
    content: Optional[str] = None
    is_partial: bool = False


class ReadFileState:
    """Tracks which files have been read and detects staleness."""

    def __init__(self) -> None:
        self._records: dict[str, _FileReadRecord] = {}

    def _normalize(self, path: str) -> str:
        return str(Path(path).resolve())

    def record_read(
        self, path: str, *, content: Optional[str] = None, is_partial: bool = False
    ) -> None:
        norm = self._normalize(path)
        try:
            mtime = os.stat(norm).st_mtime
        except OSError:
            mtime = 0.0
        self._records[norm] = _FileReadRecord(
            timestamp=mtime, content=content, is_partial=is_partial
        )

    def has_been_read(self, path: str) -> bool:
        return self._normalize(path) in self._records

    def is_partial_view(self, path: str) -> bool:
        record = self._records.get(self._normalize(path))
        return record.is_partial if record else False

    def is_stale(self, path: str) -> bool:
        norm = self._normalize(path)
        record = self._records.get(norm)
        if record is None:
            return False
        try:
            current_mtime = os.stat(norm).st_mtime
        except OSError:
            return False
        if current_mtime <= record.timestamp:
            return False
        # mtime changed -- fall back to content comparison if we have full content
        if record.content is not None and not record.is_partial:
            try:
                current_content = Path(norm).read_text(encoding="utf-8")
                if current_content == record.content:
                    return False
            except OSError:
                pass
        return True

    def clear(self, path: Optional[str] = None) -> None:
        if path:
            self._records.pop(self._normalize(path), None)
        else:
            self._records.clear()
