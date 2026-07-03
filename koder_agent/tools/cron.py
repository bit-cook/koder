"""CronCreate, CronDelete, CronList tools."""

from __future__ import annotations

import json

from koder_agent.harness.cron.expression import human_schedule, validate_cron
from koder_agent.harness.cron.storage import (
    CronStorage,
    default_cron_storage,
    set_default_cron_storage,
)

from .compat import function_tool


def _get_cron_storage() -> CronStorage:
    return default_cron_storage()


def _set_cron_storage(storage: CronStorage | None) -> None:
    set_default_cron_storage(storage)


def _validate_cron(expr: str) -> str | None:
    """Validate a 5-field cron expression. Returns error or None."""
    return validate_cron(expr)


def _human_schedule(cron: str) -> str:
    """Convert cron expression to rough human-readable schedule."""
    return human_schedule(cron)


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
