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

from koder_agent.harness.config.service import RuntimeConfigService


def test_runtime_config_precedence_prefers_cli_over_env_and_file(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    service = RuntimeConfigService(config_path)
    config = service.load()
    config.model.name = "from-file"
    service.save(config)

    monkeypatch.setenv("KODER_MODEL", "from-env")
    assert (
        service.get_effective_value("from-file", "KODER_MODEL", cli_value="from-cli") == "from-cli"
    )
    assert service.get_effective_value("from-file", "KODER_MODEL") == "from-env"
