"""Tests for task persistence and extended models."""

from koder_agent.harness.tasks.models import TaskRecord
from koder_agent.harness.tasks.service import TaskService
from koder_agent.harness.tasks.storage import TaskStorage


def test_task_record_has_required_fields():
    """TaskRecord must carry description, owner, blocks, blockedBy, metadata."""
    task = TaskRecord.create(task_id="1", title="Test task", description="do stuff")
    assert task.description == "do stuff"
    assert task.owner is None
    assert task.blocks == []
    assert task.blocked_by == []
    assert task.metadata == {}
    assert task.status == "pending"


def test_task_record_to_dict_roundtrip():
    task = TaskRecord.create(task_id="2", title="RT", description="round trip")
    d = task.to_dict()
    restored = TaskRecord.from_dict(d)
    assert restored == task
    assert restored.id == "2"


def test_storage_create_and_get(tmp_path):
    storage = TaskStorage(tmp_path / "tasks")
    task = storage.create("Build feature", description="implement X")
    assert task.id == "1"
    assert task.title == "Build feature"
    assert task.description == "implement X"

    retrieved = storage.get(task.id)
    assert retrieved == task


def test_storage_list_tasks(tmp_path):
    storage = TaskStorage(tmp_path / "tasks")
    storage.create("Task A")
    storage.create("Task B")
    tasks = storage.list_all()
    assert len(tasks) == 2
    assert tasks[0].id == "1"
    assert tasks[1].id == "2"


def test_storage_update_status(tmp_path):
    storage = TaskStorage(tmp_path / "tasks")
    task = storage.create("Task")
    updated = storage.update(task.id, status="in_progress")
    assert updated.status == "in_progress"
    # Verify persisted
    reloaded = storage.get(task.id)
    assert reloaded.status == "in_progress"


def test_storage_delete_preserves_high_water_mark(tmp_path):
    storage = TaskStorage(tmp_path / "tasks")
    storage.create("A")
    storage.create("B")
    storage.delete("1")
    task3 = storage.create("C")
    assert task3.id == "3"  # ID 1 not reused


def test_storage_add_blocks(tmp_path):
    storage = TaskStorage(tmp_path / "tasks")
    t1 = storage.create("Blocker")
    t2 = storage.create("Blocked")
    storage.add_block(blocker_id=t1.id, blocked_id=t2.id)
    t1r = storage.get(t1.id)
    t2r = storage.get(t2.id)
    assert t2.id in t1r.blocks
    assert t1.id in t2r.blocked_by


def test_storage_list_filters_completed_blockers(tmp_path):
    storage = TaskStorage(tmp_path / "tasks")
    t1 = storage.create("Blocker")
    t2 = storage.create("Blocked")
    storage.add_block(blocker_id=t1.id, blocked_id=t2.id)
    storage.update(t1.id, status="completed")
    tasks = storage.list_all(filter_resolved_blockers=True)
    blocked = next(t for t in tasks if t.id == t2.id)
    assert blocked.blocked_by == []  # Completed blocker filtered out


def test_task_service_with_storage_backend(tmp_path):
    storage = TaskStorage(tmp_path / "tasks")
    svc = TaskService(storage=storage)
    task = svc.create_task("Test", description="d")
    assert task.id == "1"
    retrieved = svc.get_task(task.id)
    assert retrieved.title == "Test"


def test_task_service_in_memory_still_works():
    svc = TaskService.in_memory()
    task = svc.create_task("In-mem")
    assert task.title == "In-mem"
