"""Cached project file index for @ autocomplete fuzzy search."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

_WALK_EXCLUDES = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".eggs",
    }
)

MAX_FILES = 50_000


def _fuzzy_score(query: str, path: str) -> int:
    """Score *path* against a fuzzy *query*.  Higher = better.  -1 = no match."""
    ql = query.lower()
    pl = path.lower()

    # Fast path: exact substring match
    if ql in pl:
        idx = pl.index(ql)
        # Bonus for matching at a component boundary (after / or start)
        bonus = 10 if idx == 0 or pl[idx - 1] == "/" else 0
        return 100 + bonus - len(path) // 10

    # Character-by-character fuzzy match
    qi = 0
    score = 0
    last_match = -1
    for pi, ch in enumerate(pl):
        if qi < len(ql) and ch == ql[qi]:
            if last_match == pi - 1:
                score += 5  # consecutive
            else:
                score += 1
            if pi == 0 or pl[pi - 1] in ("/", "_", "-", "."):
                score += 3  # boundary
            last_match = pi
            qi += 1

    if qi < len(ql):
        return -1  # not all query chars matched

    return score - len(path) // 10


def find_common_prefix(strings: list[str]) -> str:
    """Return the longest common prefix shared by all *strings*."""
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        i = 0
        while i < len(prefix) and i < len(s) and prefix[i] == s[i]:
            i += 1
        prefix = prefix[:i]
        if not prefix:
            return ""
    return prefix


class ProjectFileIndex:
    """Cached index of project files for autocomplete."""

    def __init__(self, cwd: str | Path, *, ttl_seconds: float = 30.0):
        self._cwd = Path(cwd)
        self._ttl = ttl_seconds
        self._files: list[str] = []
        self._last_refresh: float = 0.0

    def get_files(self) -> list[str]:
        """Return cached file list, refreshing if TTL expired."""
        now = time.monotonic()
        if now - self._last_refresh >= self._ttl:
            self._refresh()
            self._last_refresh = now
        return self._files

    def _refresh(self) -> None:
        """Populate ``_files`` from *git ls-files* or a directory walk."""
        files = self._git_ls_files()
        if files is None:
            files = self._walk_files()
        self._files = files[:MAX_FILES]

    def _git_ls_files(self) -> list[str] | None:
        """Return relative paths via ``git ls-files``, or *None* on failure."""
        try:
            result = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                cwd=str(self._cwd),
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                return None
            paths = [line for line in result.stdout.splitlines() if line]
            return sorted(paths)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return None

    def _walk_files(self) -> list[str]:
        """Fallback: recursive walk with common exclusions."""
        paths: list[str] = []
        try:
            for item in sorted(self._cwd.rglob("*")):
                if len(paths) >= MAX_FILES:
                    break
                # Skip excluded directories
                parts = item.relative_to(self._cwd).parts
                if any(part in _WALK_EXCLUDES for part in parts):
                    continue
                if item.is_file():
                    paths.append(str(item.relative_to(self._cwd)))
        except (OSError, ValueError):
            pass
        return paths

    def search(self, query: str, *, max_results: int = 15) -> list[str]:
        """Fuzzy-search files matching *query*.  Returns up to *max_results*."""
        files = self.get_files()
        if not query:
            # Return first few files when query is empty
            return files[:max_results]

        scored: list[tuple[int, str]] = []
        for path in files:
            s = _fuzzy_score(query, path)
            if s >= 0:
                scored.append((s, path))

        scored.sort(key=lambda t: (-t[0], t[1]))
        return [path for _, path in scored[:max_results]]
