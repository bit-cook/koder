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

from koder_agent.harness.permissions.persistence import PermissionStore
from koder_agent.harness.permissions.service import PermissionService


def test_permission_store_persists_allow_rule(tmp_path):
    store = PermissionStore(tmp_path / "permissions.json")
    service = PermissionService.default(store=store)

    service.add_rule("run_shell", "allow", "touch *")

    reloaded = PermissionService.default(store=store)
    result = reloaded.evaluate_tool_call("run_shell", {"command": "touch foo.txt"})
    assert result.allowed is True
    assert result.requires_approval is False
