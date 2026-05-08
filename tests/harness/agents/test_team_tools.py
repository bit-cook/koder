import asyncio
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


def test_team_create_returns_team_info(tmp_path):
    from koder_agent.harness.agents.teams.service import TeamService
    from koder_agent.tools.team import _team_create_impl

    svc = TeamService.for_test(root=tmp_path)

    result = asyncio.run(
        _team_create_impl(
            team_name="my-project",
            description="Project team",
            _team_service=svc,
        )
    )
    parsed = json.loads(result)
    assert parsed["status"] == "created"
    assert parsed["team_name"] == "my-project"
    assert "team_id" in parsed
    assert "config_path" in parsed
    assert "lead_agent_id" in parsed


def test_team_create_handles_duplicate_name(tmp_path):
    from koder_agent.harness.agents.teams.service import TeamService
    from koder_agent.tools.team import _team_create_impl

    svc = TeamService.for_test(root=tmp_path)

    asyncio.run(_team_create_impl(team_name="alpha", _team_service=svc))
    result = asyncio.run(_team_create_impl(team_name="alpha", _team_service=svc))
    parsed = json.loads(result)
    # Should either succeed with a different team_id or report error
    assert parsed["status"] in {"created", "error"}


def test_team_delete_succeeds_when_no_active_members(tmp_path):
    from koder_agent.harness.agents.teams.service import TeamService
    from koder_agent.tools.team import _team_create_impl, _team_delete_impl

    svc = TeamService.for_test(root=tmp_path)
    create_result = asyncio.run(_team_create_impl(team_name="temp-team", _team_service=svc))
    team_id = json.loads(create_result)["team_id"]

    result = asyncio.run(_team_delete_impl(_team_service=svc, _team_id=team_id))
    parsed = json.loads(result)
    assert parsed["status"] == "deleted"
    assert parsed["team_name"] == "temp-team"
    assert parsed["team_id"] == team_id


def test_team_delete_fails_with_active_members(tmp_path):
    from koder_agent.harness.agents.teams.service import TeamService
    from koder_agent.tools.team import _team_create_impl, _team_delete_impl

    svc = TeamService.for_test(root=tmp_path)
    create_result = asyncio.run(_team_create_impl(team_name="active-team", _team_service=svc))
    team_id = json.loads(create_result)["team_id"]
    svc.add_member(team_id, "worker-1", name="worker", is_active=True)

    result = asyncio.run(_team_delete_impl(_team_service=svc, _team_id=team_id))
    parsed = json.loads(result)
    assert parsed["status"] == "error"
    assert "active" in parsed["error"].lower()
