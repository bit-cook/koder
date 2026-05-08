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


def test_debug_and_diagnostic_commands_available():
    """Debug and diagnostic commands are registered in the full registry."""
    registry = CommandRegistry.with_all_commands()
    names = set(registry.list_names())
    expected_debug = {
        "bughunter",
        "debug-tool-call",
        "version",
        "env",
        "issue",
        "summary",
        "rate-limit-options",
        "oauth-refresh",
    }
    assert expected_debug <= names


def test_debug_commands_not_in_default_registry():
    """Debug commands are excluded from the default registry."""
    defaults = set(CommandRegistry.with_defaults().list_names())
    assert "version" not in defaults
    assert "heapdump" not in defaults
