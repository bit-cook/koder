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

from koder_agent.harness.agents.teams import TeamService


def test_team_membership_round_trips_in_order(tmp_path):
    service = TeamService.for_test(root=tmp_path / ".koder", cwd=tmp_path)
    team_id = service.create_team("reviewers")
    service.add_member(team_id, "agent-1")
    service.add_member(team_id, "agent-2")
    assert service.members(team_id) == ["agent-1", "agent-2"]
