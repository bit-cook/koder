import asyncio
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

from koder_agent.agentic.approval_hooks import ApprovalHooks
from koder_agent.agentic.hooks import ToolDisplayHooks


def test_stop_hook_can_block_agent_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"decision\\":\\"block\\",\\"reason\\":\\"keep going\\"}\')"',
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    hooks = ApprovalHooks(ToolDisplayHooks(streaming_mode=True))

    class _Agent:
        name = "main"

    try:
        asyncio.run(hooks.on_agent_end(None, _Agent(), "done"))
        assert False, "expected Stop hook to block"
    except RuntimeError as exc:
        assert "keep going" in str(exc)
