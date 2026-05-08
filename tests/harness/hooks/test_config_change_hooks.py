import json
import sys
import types
from pathlib import Path

if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.config.schema import RuntimeConfig
from koder_agent.harness.config.service import RuntimeConfigService


def test_runtime_config_save_dispatches_config_change_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    marker = tmp_path / "config-change.json"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "ConfigChange": [
                        {
                            "matcher": "user_settings",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    service = RuntimeConfigService(config_path=tmp_path / ".koder" / "config.yaml")
    service.save(RuntimeConfig())

    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["event"] == "ConfigChange"
    assert payload["source"] == "user_settings"


def test_runtime_config_save_can_be_blocked_by_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "ConfigChange": [
                        {
                            "matcher": "user_settings",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"blocked change\\"}\')"',
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    service = RuntimeConfigService(config_path=tmp_path / ".koder" / "config.yaml")
    try:
        service.save(RuntimeConfig())
        assert False, "expected ConfigChange hook to block"
    except RuntimeError as exc:
        assert "blocked change" in str(exc)
