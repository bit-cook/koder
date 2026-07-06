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

from koder_agent.harness.commands.registry import CommandRegistry  # noqa: E402


def test_command_registry_contains_session_domain():
    registry = CommandRegistry.with_defaults()
    assert {"session", "resume", "rename", "status", "usage", "effort"} <= set(
        registry.list_names()
    )
