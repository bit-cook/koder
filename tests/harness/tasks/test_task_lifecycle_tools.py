"""Tests for TaskCreate, TaskUpdate, TaskGet, TaskList tools."""

import json

import pytest

from koder_agent.tools.task_lifecycle import (
    _set_task_storage,
    task_create,
    task_get,
    task_list,
    task_update,
)


@pytest.fixture(autouse=True)
def _fresh_storage(tmp_path):
    from koder_agent.harness.tasks.storage import TaskStorage

    storage = TaskStorage(tmp_path / "tasks")
    _set_task_storage(storage)
    yield
    _set_task_storage(None)


def test_task_create_returns_id_and_subject():
    result = json.loads(task_create(subject="Build feature", description="implement X"))
    assert "task" in result
    assert result["task"]["id"] == "1"
    assert result["task"]["subject"] == "Build feature"


def test_task_create_increments_ids():
    task_create(subject="A", description="a")
    r2 = json.loads(task_create(subject="B", description="b"))
    assert r2["task"]["id"] == "2"


def test_task_get_existing():
    task_create(subject="Test", description="d")
    result = json.loads(task_get(task_id="1"))
    assert result["task"]["id"] == "1"
    assert result["task"]["subject"] == "Test"
    assert result["task"]["status"] == "pending"


def test_task_get_missing():
    result = json.loads(task_get(task_id="999"))
    assert result["task"] is None


def test_task_list_empty():
    result = json.loads(task_list())
    assert result["tasks"] == []


def test_task_list_returns_all():
    task_create(subject="A", description="a")
    task_create(subject="B", description="b")
    result = json.loads(task_list())
    assert len(result["tasks"]) == 2


def test_task_update_status():
    task_create(subject="Task", description="d")
    result = json.loads(task_update(task_id="1", status="in_progress"))
    assert result["success"] is True
    assert "status" in result["updated_fields"]
    assert result["status_change"] == {"from": "pending", "to": "in_progress"}


def test_task_update_not_found():
    result = json.loads(task_update(task_id="999", status="completed"))
    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_task_update_delete():
    task_create(subject="Temp", description="d")
    result = json.loads(task_update(task_id="1", status="deleted"))
    assert result["success"] is True
    get_result = json.loads(task_get(task_id="1"))
    assert get_result["task"] is None


def test_task_update_add_blocks():
    task_create(subject="Blocker", description="b")
    task_create(subject="Blocked", description="b")
    result = json.loads(task_update(task_id="1", add_blocks=["2"]))
    assert result["success"] is True
    t2 = json.loads(task_get(task_id="2"))
    assert "1" in t2["task"]["blocked_by"]
