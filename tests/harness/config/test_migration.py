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

from koder_agent.harness.config.migration import migrate_config_file


def test_migrate_config_file_creates_backup_before_rewrite(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("model:\n  name: gpt-4.1\n", encoding="utf-8")
    result = migrate_config_file(config_path)
    assert result.backup_path.exists()
    assert config_path.exists()
