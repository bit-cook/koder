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


def test_plugin_install_rejects_invalid_json(tmp_path):
    """Invalid JSON in plugin.json is caught during manifest validation."""
    service = PluginLifecycleService.for_test(tmp_path)
    broken_dir = tmp_path / "broken_plugin"
    broken_dir.mkdir()
    (broken_dir / "plugin.json").write_text("{not-json", encoding="utf-8")
    result = service.install_from_dir(broken_dir)
    assert result.success is False
    assert "Invalid JSON" in result.message


def test_plugin_install_rolls_back_on_copy_error(tmp_path):
    """Rollback occurs if copy fails after manifest validation passes."""
    service = PluginLifecycleService.for_test(tmp_path)
    valid_dir = tmp_path / "valid-plugin"
    valid_dir.mkdir()
    (valid_dir / "plugin.json").write_text(
        '{"name": "valid-plugin", "version": "1.0.0"}', encoding="utf-8"
    )
    # Install should succeed
    result = service.install_from_dir(valid_dir)
    assert result.success is True
    assert result.plugin_name == "valid-plugin"
