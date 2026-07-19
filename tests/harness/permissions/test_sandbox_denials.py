"""Sandbox-related permission denials for unsupported execution surfaces."""

from __future__ import annotations

import json

from koder_agent.harness.permissions.service import PermissionService


def _enable_sandbox(project) -> None:
    (project / ".koder").mkdir(parents=True)
    (project / ".koder" / "settings.local.json").write_text(
        json.dumps({"sandbox": {"enabled": True, "backend": "unix-local"}}),
        encoding="utf-8",
    )


def test_powershell_denied_under_sandbox_with_actionable_message(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    _enable_sandbox(project)
    monkeypatch.chdir(project)
    service = PermissionService.default()

    decision = service.evaluate_tool_call("run_powershell", {"command": "Get-ChildItem"})

    assert not decision.allowed
    assert decision.requires_approval
    assert "sandbox protection is unavailable for PowerShell" in decision.reason
    assert "loses host-process" in decision.reason
    assert "requires explicit approval" in decision.reason


def test_background_shell_denied_under_sandbox_with_actionable_message(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    _enable_sandbox(project)
    monkeypatch.chdir(project)
    service = PermissionService.default()

    decision = service.evaluate_tool_call(
        "run_shell", {"command": "sleep 5", "run_in_background": True}
    )

    assert not decision.allowed
    assert "background sandbox execution is not implemented" in decision.reason
    assert "foreground" in decision.reason
    assert "/sandbox exclude" in decision.reason
    assert "/sandbox disable" in decision.reason
