import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.tasks.service import TaskService


def test_task_service_creates_and_lists_tasks():
    service = TaskService.in_memory()
    task = service.create_task("draft manifest")
    assert service.list_tasks()[0].id == task.id


def test_task_service_can_get_update_and_emit_output_records():
    service = TaskService.in_memory()
    task = service.create_task("draft manifest")
    service.update_status(task.id, "done")
    assert service.get_task(task.id).status == "done"
    output = service.get_output(task.id)
    assert output is not None
    assert output.task_id == task.id
    assert output.status == "done"
