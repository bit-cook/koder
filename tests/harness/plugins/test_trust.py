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

from koder_agent.harness.plugins.validator import PluginTrustService


def test_trust_service_requires_ack_for_untrusted_plugin():
    service = PluginTrustService.for_test()
    result = service.evaluate("sample-untrusted-plugin")
    assert result.requires_trust_ack is True
