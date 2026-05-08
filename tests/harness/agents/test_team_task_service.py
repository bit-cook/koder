import json
import sys
import types
from pathlib import Path

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.agents.teams import TeamService, TeamTaskService


def test_team_task_service_persists_tasks_and_claims_with_blockers(tmp_path):
    service = TeamTaskService.for_test(
        "reviewers", root=tmp_path / ".koder" / "tasks", cwd=tmp_path
    )
    first = service.create_task("Audit auth flow")
    second = service.create_task("Patch auth flow", blocked_by=[first.id])
    third = service.create_task("Document auth flow")

    blocked = service.claim_task(second.id, "agent-2", check_agent_busy=True)
    claimed = service.claim_task(first.id, "agent-1", check_agent_busy=True)
    busy = service.claim_task(third.id, "agent-1", check_agent_busy=True)
    service.update_status(first.id, "completed")
    unblocked = service.claim_task(second.id, "agent-2", check_agent_busy=True)

    assert blocked.success is False
    assert blocked.reason == "blocked"
    assert claimed.success is True
    assert busy.success is False
    assert busy.reason == "agent_busy"
    assert unblocked.success is True
    assert (service.task_dir / f"{first.id}.json").exists()


def test_team_task_service_dispatches_task_hooks_from_koder_settings(tmp_path):
    hook_dir = tmp_path / ".koder"
    hook_dir.mkdir(parents=True)
    created_path = tmp_path / "task-created.json"
    completed_path = tmp_path / "task-completed.json"
    (hook_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "TaskCreated": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{created_path}').write_text(sys.stdin.read())\"",
                                }
                            ]
                        }
                    ],
                    "TaskCompleted": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{completed_path}').write_text(sys.stdin.read())\"",
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    service = TeamTaskService.for_test(
        "reviewers", root=tmp_path / ".koder" / "tasks", cwd=tmp_path
    )
    task = service.create_task("Audit auth flow")
    service.update_status(task.id, "completed")

    created_payload = json.loads(created_path.read_text(encoding="utf-8"))
    completed_payload = json.loads(completed_path.read_text(encoding="utf-8"))
    assert created_payload["event"] == "TaskCreated"
    assert completed_payload["event"] == "TaskCompleted"


def test_team_service_dispatches_teammate_idle_hook_from_koder_settings(tmp_path):
    hook_dir = tmp_path / ".koder"
    hook_dir.mkdir(parents=True)
    idle_path = tmp_path / "teammate-idle.json"
    (hook_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "TeammateIdle": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{idle_path}').write_text(sys.stdin.read())\"",
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    service = TeamService.for_test(root=tmp_path / ".koder", cwd=tmp_path)
    team_id = service.create_team("reviewers")
    service.add_member(team_id, "agent-1", name="reviewer-1", cwd=tmp_path)
    service.set_member_active(team_id, "agent-1", False)

    payload = json.loads(idle_path.read_text(encoding="utf-8"))
    assert payload["event"] == "TeammateIdle"
    assert payload["agent_name"] == "reviewer-1"


def test_teammate_idle_hook_can_block_idle_transition(tmp_path):
    hook_dir = tmp_path / ".koder"
    hook_dir.mkdir(parents=True)
    (hook_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "TeammateIdle": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"keep working\\"}\')"',
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    service = TeamService.for_test(root=tmp_path / ".koder", cwd=tmp_path)
    team_id = service.create_team("reviewers")
    service.add_member(team_id, "agent-1", name="reviewer-1", cwd=tmp_path)

    try:
        service.set_member_active(team_id, "agent-1", False)
        assert False, "expected TeammateIdle hook to block"
    except RuntimeError as exc:
        assert "keep working" in str(exc)


def test_team_task_hooks_can_block_create_and_complete(tmp_path):
    hook_dir = tmp_path / ".koder"
    hook_dir.mkdir(parents=True)
    (hook_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "TaskCreated": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"no new tasks\\"}\')"',
                                }
                            ]
                        }
                    ],
                    "TaskCompleted": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"not yet\\"}\')"',
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    service = TeamTaskService.for_test(
        "reviewers", root=tmp_path / ".koder" / "tasks", cwd=tmp_path
    )
    try:
        service.create_task("Blocked task")
        assert False, "expected TaskCreated hook to block"
    except RuntimeError as exc:
        assert "no new tasks" in str(exc)

    (hook_dir / "settings.json").write_text(
        json.dumps({"hooks": {}}),
        encoding="utf-8",
    )
    created = service.create_task("Allowed task")
    (hook_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "TaskCompleted": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"not yet\\"}\')"',
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    try:
        service.update_status(created.id, "completed")
        assert False, "expected TaskCompleted hook to block"
    except RuntimeError as exc:
        assert "not yet" in str(exc)
