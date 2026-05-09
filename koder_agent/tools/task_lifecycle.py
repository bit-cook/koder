"""TaskCreate, TaskUpdate, TaskGet, TaskList tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from koder_agent.harness.tasks.storage import TaskStorage

from .compat import function_tool

# --- Storage singleton ---

_storage: TaskStorage | None = None


def _get_task_storage() -> TaskStorage:
    global _storage
    if _storage is None:
        list_id = os.environ.get("KODER_TASK_LIST_ID", "default")
        root = Path.home() / ".koder" / "tasks" / list_id
        _storage = TaskStorage(root)
    return _storage


def _set_task_storage(storage: TaskStorage | None) -> None:
    """Override storage for testing."""
    global _storage
    _storage = storage


# --- Pydantic models ---


class TaskCreateModel(BaseModel):
    subject: str = Field(..., description="Brief task title")
    description: str = Field(..., description="What needs to be done")
    metadata: Optional[str] = Field(default=None, description="JSON string of key-value pairs")


class TaskUpdateModel(BaseModel):
    task_id: str = Field(..., description="Task ID to update")
    subject: Optional[str] = Field(default=None, description="Change task title")
    description: Optional[str] = Field(default=None, description="Change description")
    status: Optional[str] = Field(
        default=None, description="pending | in_progress | completed | deleted"
    )
    owner: Optional[str] = Field(default=None, description="Agent name for assignment")
    add_blocks: Optional[List[str]] = Field(
        default=None, description="Task IDs this task should block"
    )
    add_blocked_by: Optional[List[str]] = Field(
        default=None, description="Task IDs that should block this task"
    )
    metadata: Optional[str] = Field(
        default=None, description="JSON string to merge into existing metadata"
    )


class TaskGetModel(BaseModel):
    task_id: str = Field(..., description="Task ID to retrieve")


class TaskListModel(BaseModel):
    pass


# --- Plain implementations (directly callable, used by tests) ---


def task_create(
    subject: str,
    description: str,
    metadata: Optional[str] = None,
) -> str:
    """Create a new task. Returns the task ID and subject.

    Args:
        subject: Brief task title.
        description: What needs to be done.
        metadata: Optional JSON string of key-value pairs, e.g. '{"priority": "high"}'.
    """
    storage = _get_task_storage()
    meta_dict: dict[str, Any] | None = None
    if metadata is not None:
        try:
            meta_dict = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            meta_dict = None
    task = storage.create(subject, description=description, metadata=meta_dict)
    return json.dumps({"task": {"id": task.id, "subject": task.title}})


def task_update(
    task_id: str,
    subject: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    add_blocks: Optional[List[str]] = None,
    add_blocked_by: Optional[List[str]] = None,
    metadata: Optional[str] = None,
) -> str:
    """Update a task's fields, status, owner, or dependencies.

    Args:
        task_id: Task ID to update.
        subject: Change task title.
        description: Change description.
        status: pending | in_progress | completed | deleted.
        owner: Agent name for assignment.
        add_blocks: Task IDs this task should block.
        add_blocked_by: Task IDs that should block this task.
        metadata: JSON string to merge into existing metadata, e.g. '{"key": "value"}'.
    """
    storage = _get_task_storage()
    meta_dict: dict[str, Any] | None = None
    if metadata is not None:
        try:
            meta_dict = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            meta_dict = None

    # Handle deletion
    if status == "deleted":
        task = storage.get(task_id)
        if task is None:
            return json.dumps(
                {
                    "success": False,
                    "task_id": task_id,
                    "updated_fields": [],
                    "error": "Task not found",
                }
            )
        old_status = task.status
        storage.delete(task_id)
        return json.dumps(
            {
                "success": True,
                "task_id": task_id,
                "updated_fields": ["status"],
                "status_change": {"from": old_status, "to": "deleted"},
            }
        )

    # Get current task
    current = storage.get(task_id)
    if current is None:
        return json.dumps(
            {
                "success": False,
                "task_id": task_id,
                "updated_fields": [],
                "error": "Task not found",
            }
        )

    old_status = current.status
    updated_fields: list[str] = []

    # Apply field updates
    update_kwargs: dict[str, Any] = {}
    if subject is not None and subject != current.title:
        update_kwargs["title"] = subject
        updated_fields.append("subject")
    if description is not None and description != current.description:
        update_kwargs["description"] = description
        updated_fields.append("description")
    if status is not None and status != current.status:
        update_kwargs["status"] = status
        updated_fields.append("status")
    if owner is not None:
        update_kwargs["owner"] = owner
        updated_fields.append("owner")
    if meta_dict is not None:
        update_kwargs["metadata"] = meta_dict
        updated_fields.append("metadata")

    if update_kwargs:
        storage.update(task_id, **update_kwargs)

    # Handle dependency additions
    if add_blocks:
        for blocked_id in add_blocks:
            storage.add_block(blocker_id=task_id, blocked_id=blocked_id)
        updated_fields.append("blocks")

    if add_blocked_by:
        for blocker_id in add_blocked_by:
            storage.add_block(blocker_id=blocker_id, blocked_id=task_id)
        updated_fields.append("blocked_by")

    result: dict[str, Any] = {
        "success": True,
        "task_id": task_id,
        "updated_fields": updated_fields,
    }
    if status is not None and status != old_status:
        result["status_change"] = {"from": old_status, "to": status}

    return json.dumps(result)


def task_get(task_id: str) -> str:
    """Get a single task's details by ID."""
    storage = _get_task_storage()
    task = storage.get(task_id)
    if task is None:
        return json.dumps({"task": None})

    return json.dumps(
        {
            "task": {
                "id": task.id,
                "subject": task.title,
                "description": task.description,
                "status": task.status,
                "owner": task.owner,
                "blocks": task.blocks,
                "blocked_by": task.blocked_by,
            }
        }
    )


def task_list() -> str:
    """List all tasks with their current status."""
    storage = _get_task_storage()
    tasks = storage.list_all(filter_resolved_blockers=True)
    return json.dumps(
        {
            "tasks": [
                {
                    "id": t.id,
                    "subject": t.title,
                    "status": t.status,
                    "owner": t.owner,
                    "blocked_by": t.blocked_by,
                }
                for t in tasks
            ]
        }
    )


# --- @function_tool wrappers for agent registration ---

task_create_tool = function_tool(task_create)
task_update_tool = function_tool(task_update)
task_get_tool = function_tool(task_get)
task_list_tool = function_tool(task_list)
