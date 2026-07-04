# ruff: noqa: E402

import asyncio
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

from koder_agent.harness.permissions.service import PermissionService
from koder_agent.harness.sandbox.registry import get_backend_status
from koder_agent.harness.tools.registry import ToolRegistry


def test_shell_tool_respects_permission_gate():
    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    result = asyncio.run(registry.get("run_shell").invoke({"command": "touch foo.txt"}))

    assert result["status"] == "approval_required"
    assert result["permission"]["tool"] == "run_shell"


def test_shell_tool_sandbox_returns_error_before_execution(tmp_path, monkeypatch):
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

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    result = asyncio.run(registry.get("run_shell").invoke({"command": "touch foo.txt"}))

    assert result["status"] == "error"
    assert "configured backend is unavailable" in result["content"]


def test_shell_tool_runs_mutating_command_in_real_sandbox(tmp_path, monkeypatch):
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
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(project)

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    result = asyncio.run(registry.get("run_shell").invoke({"command": "touch sandboxed.txt"}))

    assert result["status"] == "success"
    assert "sandboxed: true" in result["content"]
    assert "backend: unix-local" in result["content"]
    assert (project / "sandboxed.txt").exists()


def test_shell_tool_reports_argument_error_for_missing_command():
    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    result = asyncio.run(registry.get("run_shell").invoke({}))

    assert result["status"] == "error"
    assert result["content"] == "Missing required argument: command"
    assert "permission" not in result


def test_shell_tool_reports_argument_error_for_blank_command():
    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    result = asyncio.run(registry.get("run_shell").invoke({"command": "   "}))

    assert result["status"] == "error"
    assert result["content"] == "Missing required argument: command"
    assert "permission" not in result


def _windows_only_registry(monkeypatch):
    """Register shell tools as if running on Windows so run_powershell exists."""
    from koder_agent.harness.tools import shell as shell_module

    monkeypatch.setattr(shell_module.platform, "system", lambda: "Windows")
    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")
    return registry


def test_powershell_tool_not_registered_on_non_windows():
    import platform as platform_module

    if platform_module.system() == "Windows":
        pytest.skip("run_powershell is expected on Windows")

    registry = ToolRegistry.with_permission_service(PermissionService.default())
    registry.register_module("shell")

    assert registry.get("run_powershell") is None


def test_powershell_tool_respects_own_permission_gate(monkeypatch):
    registry = _windows_only_registry(monkeypatch)

    result = asyncio.run(registry.get("run_powershell").invoke({"command": "New-Item fixture.txt"}))

    assert result["status"] == "approval_required"
    assert result["permission"]["tool"] == "run_powershell"


def test_powershell_tool_reports_argument_error_for_blank_command(monkeypatch):
    registry = _windows_only_registry(monkeypatch)

    result = asyncio.run(registry.get("run_powershell").invoke({"command": "   "}))

    assert result["status"] == "error"
    assert result["content"] == "Missing required argument: command"
    assert "permission" not in result


def test_powershell_tool_reports_missing_executable(monkeypatch):
    from koder_agent.harness.tools import shell_executor

    monkeypatch.setattr(shell_executor, "resolve_powershell_executable", lambda: None)
    registry = _windows_only_registry(monkeypatch)

    result = asyncio.run(registry.get("run_powershell").invoke({"command": "Get-Location"}))

    assert result["status"] == "error"
    assert "PowerShell executable not found" in result["content"]
