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

from koder_agent.harness.plugins.catalog import PluginCatalog


def test_plugin_catalog_marks_trust_required_for_untrusted_plugin():
    catalog = PluginCatalog.for_test()
    plugin = catalog.get("sample-untrusted-plugin")
    assert plugin.requires_trust_ack is True
