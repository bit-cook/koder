"""CronCreate, CronDelete, CronList tools."""

from __future__ import annotations

import json
import re
from pathlib import Path

from koder_agent.harness.cron.storage import CronStorage

from .compat import function_tool

# --- Storage singleton ---

_storage: CronStorage | None = None


def _get_cron_storage() -> CronStorage:
    global _storage
    if _storage is None:
        root = Path.home() / ".koder"
        _storage = CronStorage(root / "scheduled_tasks.json")
    return _storage


def _set_cron_storage(storage: CronStorage | None) -> None:
    global _storage
    _storage = storage


# --- Cron validation ---

_CRON_FIELD_RE = re.compile(r"^(\*|(\d+(-\d+)?(,\d+(-\d+)?)*)(\/\d+)?|\*\/\d+)$")


def _validate_cron(expr: str) -> str | None:
    """Validate a 5-field cron expression. Returns error or None."""
    fields = expr.strip().split()
    if len(fields) != 5:
        return f"Expected 5 fields (M H DoM Mon DoW), got {len(fields)}"
    for i, field in enumerate(fields):
        if not _CRON_FIELD_RE.match(field):
            return f"Invalid cron field {i + 1}: {field}"
    return None


def _human_schedule(cron: str) -> str:
    """Convert cron expression to rough human-readable schedule."""
    fields = cron.strip().split()
    if len(fields) != 5:
        return cron
    minute, hour, dom, month, dow = fields

    parts = []
    if dow != "*":
        day_names = {
            "0": "Sun",
            "1": "Mon",
            "2": "Tue",
            "3": "Wed",
            "4": "Thu",
            "5": "Fri",
            "6": "Sat",
            "7": "Sun",
        }
        days = [day_names.get(d, d) for d in dow.split(",")]
        parts.append(f"on {','.join(days)}")

    if hour != "*" and minute != "*":
        parts.append(f"at {hour}:{minute.zfill(2)}")
    elif hour != "*":
        parts.append(f"at {hour}:00")

    return " ".join(parts) if parts else cron


# --- Plain implementations ---


def cron_create(cron: str, prompt: str, recurring: bool = True) -> str:
    """Create a scheduled task that runs on a cron schedule."""
    error = _validate_cron(cron)
    if error:
        return json.dumps({"error": f"Invalid cron expression: {error}"})

    storage = _get_cron_storage()
    try:
        job = storage.create(cron=cron, prompt=prompt, recurring=recurring)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    return json.dumps(
        {
            "id": job["id"],
            "human_schedule": _human_schedule(cron),
            "recurring": recurring,
        }
    )


def cron_delete(id: str) -> str:
    """Delete a scheduled cron job by its ID."""
    storage = _get_cron_storage()
    if storage.delete(id):
        return json.dumps({"id": id})
    return json.dumps({"error": f"Job {id} not found"})


def cron_list() -> str:
    """List all scheduled cron jobs."""
    storage = _get_cron_storage()
    jobs = storage.list_all()
    return json.dumps(
        {
            "jobs": [
                {
                    "id": j["id"],
                    "cron": j["cron"],
                    "human_schedule": _human_schedule(j["cron"]),
                    "prompt": j["prompt"][:80],
                    "recurring": j.get("recurring", True),
                }
                for j in jobs
            ]
        }
    )


# --- @function_tool wrappers ---

cron_create_tool = function_tool(cron_create)
cron_delete_tool = function_tool(cron_delete)
cron_list_tool = function_tool(cron_list)
