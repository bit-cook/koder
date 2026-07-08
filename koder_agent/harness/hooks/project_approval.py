"""Project-level hook trust gate.

Prevents hooks from untrusted project .koder/settings.json from executing
without explicit user approval — analogous to MCP's project_approvals.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_APPROVALS_FILE = "hook-project-approvals.json"

# In-process cache: avoids re-reading + JSON-parsing the approvals file on
# every hook dispatch (2+ per tool call). Invalidated by mtime change or
# when approve/revoke modifies the file within this process.
_cache_data: dict | None = None
_cache_mtime: float = 0.0


def _approvals_path() -> Path:
    return Path.home() / ".koder" / _APPROVALS_FILE


def _project_key(project_root: Path) -> str:
    return hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:16]


def _load_approvals() -> dict:
    """Load approvals with mtime-based caching."""
    global _cache_data, _cache_mtime
    path = _approvals_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _cache_data = None
        _cache_mtime = 0.0
        return {}
    if _cache_data is not None and mtime == _cache_mtime:
        return _cache_data
    try:
        _cache_data = json.loads(path.read_text(encoding="utf-8"))
        _cache_mtime = mtime
    except (json.JSONDecodeError, OSError):
        _cache_data = None
        _cache_mtime = 0.0
        return {}
    return _cache_data


def _invalidate_cache() -> None:
    global _cache_data, _cache_mtime
    _cache_data = None
    _cache_mtime = 0.0


def is_project_hooks_allowed(project_root: Path) -> bool:
    """Check if hooks from this project directory have been approved."""
    data = _load_approvals()
    if not data:
        return False
    return _project_key(project_root) in data.get("approved", {})


def approve_project_hooks(project_root: Path) -> None:
    """Persist approval for hooks from this project directory."""
    path = _approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_approvals() or {}
    approved = data.setdefault("approved", {})
    key = _project_key(project_root)
    approved[key] = str(project_root.resolve())
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _invalidate_cache()


def revoke_project_hooks(project_root: Path) -> None:
    """Remove approval for this project's hooks."""
    data = _load_approvals()
    if not data:
        return
    data.get("approved", {}).pop(_project_key(project_root), None)
    try:
        path = _approvals_path()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
    _invalidate_cache()
