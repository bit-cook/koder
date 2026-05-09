import json
import sys
import types
from pathlib import Path

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.permissions.modes import PermissionMode
from koder_agent.harness.permissions.service import PermissionService
from koder_agent.harness.sandbox.registry import get_backend_status


def test_permission_service_requires_approval_for_write_shell_command():
    service = PermissionService.default()
    result = service.evaluate_tool_call("run_shell", {"command": "touch foo.txt"})
    assert result.requires_approval is True
    assert result.allowed is False


def test_permission_service_allows_read_only_shell_command():
    service = PermissionService.default()
    result = service.evaluate_tool_call("run_shell", {"command": 'rg "TODO" src'})
    assert result.allowed is True
    assert result.requires_approval is False


def test_permission_service_allows_code_intelligence_tool():
    service = PermissionService.default()
    result = service.evaluate_tool_call("code_intelligence", {"operation": "workspace_symbols"})
    assert result.allowed is True
    assert result.requires_approval is False


def test_permission_service_bypass_mode_allows_mutation():
    service = PermissionService.default(mode=PermissionMode.BYPASS)
    result = service.evaluate_tool_call("run_shell", {"command": "touch foo.txt"})
    assert result.allowed is True
    assert result.requires_approval is False


def test_permission_service_supports_skill_rule_syntax():
    service = PermissionService.default()
    service.add_rule("Skill", "deny", "Skill(deploy *)")

    blocked = service.evaluate_tool_call("Skill", {"skill": "deploy", "arguments": "production"})
    allowed = service.evaluate_tool_call("Skill", {"skill": "review", "arguments": "123"})

    assert blocked.allowed is False
    assert blocked.requires_approval is False
    assert "Denied by rule" in blocked.reason
    assert allowed.allowed is True


def test_permission_service_sandbox_blocks_non_excluded_shell_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.local.json").write_text(
        json.dumps(
            {
                "sandbox": {
                    "enabled": True,
                    "backend": "missing",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    service = PermissionService.default()
    result = service.evaluate_tool_call("run_shell", {"command": "touch foo.txt"})

    assert result.allowed is False
    assert result.requires_approval is False
    assert "configured backend is unavailable" in result.reason


def test_permission_service_auto_allows_real_sandboxed_shell_commands(tmp_path, monkeypatch):
    status = get_backend_status("unix-local")
    if not status.available:
        pytest.skip(status.reason)
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.local.json").write_text(
        json.dumps(
            {
                "sandbox": {
                    "enabled": True,
                    "backend": "unix-local",
                    "autoAllowBashIfSandboxed": True,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    service = PermissionService.default()
    result = service.evaluate_tool_call("run_shell", {"command": "touch foo.txt"})

    assert result.allowed is True
    assert result.requires_approval is False
    assert "sandboxed shell command auto-allowed" in result.reason


def test_permission_service_sandbox_exclusions_return_to_normal_shell_permissions(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "project"
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.local.json").write_text(
        json.dumps(
            {
                "sandbox": {
                    "enabled": True,
                    "excludedCommands": ["touch *"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    service = PermissionService.default()
    result = service.evaluate_tool_call("run_shell", {"command": "touch foo.txt"})

    assert result.allowed is False
    assert result.requires_approval is True
    assert "mutate filesystem" in result.reason
