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

from koder_agent.harness.commands.registry import CommandRegistry


def test_workflow_review_commands_are_registered():
    registry = CommandRegistry.with_defaults()
    assert {
        "review",
        "compact",
        "branch",
        "rewind",
    } <= set(registry.list_names())
