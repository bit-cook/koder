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

from koder_agent.harness.permissions.modes import PermissionMode
from koder_agent.harness.permissions.propagation import propagate_permission_context
from koder_agent.harness.permissions.service import PermissionService


def test_permission_context_propagates_rules_to_worker():
    parent = PermissionService.default(mode=PermissionMode.STRICT)
    parent.add_rule("run_shell", "allow", "touch *")

    child = propagate_permission_context(parent, worker_name="worker-1")
    result = child.evaluate_tool_call("run_shell", {"command": "touch foo.txt"})

    assert child.mode == PermissionMode.STRICT
    assert child.owner == "worker-1"
    assert result.allowed is True
    assert result.requires_approval is False
