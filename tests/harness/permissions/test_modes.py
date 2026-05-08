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


def test_permission_modes_are_explicit():
    assert PermissionMode.DEFAULT.value == "default"
    assert PermissionMode.STRICT.value == "strict"
    assert PermissionMode.BYPASS.value == "bypass"
