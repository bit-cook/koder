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

from koder_agent.harness.permissions.service import PermissionService
from koder_agent.harness.tools.registry import ToolRegistry


def test_permission_request_and_notification_hooks_fire_for_approval_required_tool(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    permission_marker = tmp_path / "permission-request.json"
    notification_marker = tmp_path / "notification.json"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PermissionRequest": [
                        {
                            "matcher": "run_shell",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{permission_marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ],
                    "Notification": [
                        {
                            "matcher": "permission_prompt",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": f"python -c \"import sys, pathlib; pathlib.Path(r'{notification_marker}').write_text(sys.stdin.read())\"",
                                }
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    result = asyncio.run(registry.get("run_shell").invoke({"command": "touch foo.txt"}))

    assert result["status"] == "approval_required"
    permission_payload = json.loads(permission_marker.read_text(encoding="utf-8"))
    notification_payload = json.loads(notification_marker.read_text(encoding="utf-8"))
    assert permission_payload["event"] == "PermissionRequest"
    assert permission_payload["tool_name"] == "run_shell"
    assert notification_payload["event"] == "Notification"
    assert notification_payload["notification_type"] == "permission_prompt"


def test_permission_request_hook_can_auto_allow_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PermissionRequest": [
                        {
                            "matcher": "run_shell",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"hookSpecificOutput\\":{\\"decision\\":{\\"behavior\\":\\"allow\\",\\"updatedInput\\":{\\"command\\":\\"printf allowed\\"}}}}\')"',
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

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    result = asyncio.run(registry.get("run_shell").invoke({"command": "touch foo.txt"}))
    assert result["status"] == "success"
    assert "allowed" in str(result["content"])


def test_permission_request_hook_can_deny_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PermissionRequest": [
                        {
                            "matcher": "run_shell",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"hookSpecificOutput\\":{\\"decision\\":{\\"behavior\\":\\"deny\\",\\"message\\":\\"denied by hook\\"}}}\')"',
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

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    result = asyncio.run(registry.get("run_shell").invoke({"command": "touch foo.txt"}))
    assert result["status"] == "error"
    assert result["content"] == "denied by hook"


def test_permission_request_hook_can_return_permission_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PermissionRequest": [
                        {
                            "matcher": "run_shell",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'python -c "print(\'{\\"hookSpecificOutput\\":{\\"decision\\":{\\"behavior\\":\\"allow\\",\\"updatedPermissions\\":[{\\"type\\":\\"setMode\\",\\"mode\\":\\"bypass\\",\\"destination\\":\\"session\\"}]}}}\')"',
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

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    result = asyncio.run(registry.get("run_shell").invoke({"command": "touch foo.txt"}))
    assert result["status"] == "success"
    assert result["permission_updates"][0]["type"] == "setMode"
