import json
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

from koder_agent.harness.plugins.lifecycle import PluginLifecycleService
from koder_agent.harness.plugins.registry import PluginRegistry


def test_plugin_registry_exports_validated_plugin_descriptor(tmp_path):
    service = PluginLifecycleService.for_test(tmp_path)
    plugin_dir = tmp_path / "plugin_ok"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "demo-plugin", "version": "1.0.0"}),
        encoding="utf-8",
    )
    service.install_from_dir(plugin_dir)

    registry = PluginRegistry.from_lifecycle(service)
    assert registry.list_names() == ["demo-plugin"]
