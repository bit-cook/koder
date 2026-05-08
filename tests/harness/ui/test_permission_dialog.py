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

from koder_agent.harness.permissions.results import PermissionEvaluationResult
from koder_agent.harness.ui.permissions.dialog import PermissionDialog


def test_permission_dialog_renders_approval_request():
    dialog = PermissionDialog()
    request = PermissionEvaluationResult.approval_required(
        tool_name="run_shell",
        reason="workspace mutation requires approval",
    )

    frame = dialog.render(request)

    assert frame["title"] == "Permission Required"
    assert frame["tool"] == "run_shell"
    assert "Approve once" in frame["options"]
