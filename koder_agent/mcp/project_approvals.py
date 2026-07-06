"""Project-scoped MCP server approval state."""

import json
from pathlib import Path


def _approvals_path() -> Path:
    return Path.home() / ".koder" / "mcp-project-approvals.json"


def _load_approvals() -> dict:
    path = _approvals_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_approvals(approvals: dict) -> None:
    path = _approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(approvals, indent=2) + "\n", encoding="utf-8")


def is_project_approved(project_root: str | Path) -> bool | None:
    """Check if a project's MCP servers have been approved.

    Returns True if approved, False if rejected, None if no decision stored.
    """
    approvals = _load_approvals()
    key = str(Path(project_root).resolve())
    return approvals.get(key)


def set_project_approval(project_root: str | Path, approved: bool) -> None:
    """Store approval decision for a project's MCP servers."""
    approvals = _load_approvals()
    key = str(Path(project_root).resolve())
    approvals[key] = approved
    _save_approvals(approvals)


def is_project_connect_allowed(project_root: str | Path) -> bool:
    """Whether PROJECT-scoped MCP servers for *project_root* may be connected.

    Project ``.mcp.json`` servers run arbitrary commands / auth helpers straight
    from the repository, so they are only connected when the user has explicitly
    approved them (stored decision is ``True``). An undecided (``None``) or
    rejected (``False``) state is treated as "do not run" — a safe,
    non-interactive default for headless sessions.
    """
    return is_project_approved(project_root) is True


def reset_project_choices(project_root: str | Path | None = None) -> int:
    """Reset stored approval decisions.

    If project_root is given, only reset that project.
    If None, reset all projects.

    Returns count of cleared entries.
    """
    approvals = _load_approvals()
    if not approvals:
        return 0

    if project_root is not None:
        key = str(Path(project_root).resolve())
        if key in approvals:
            del approvals[key]
            _save_approvals(approvals)
            return 1
        return 0

    count = len(approvals)
    _save_approvals({})
    return count
