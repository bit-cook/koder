"""Exact state-bound sandbox degradation tests for run_powershell."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import koder_agent.tools.powershell as powershell
from koder_agent.harness.permissions.modes import PermissionMode
from koder_agent.harness.permissions.results import PermissionEvaluationResult
from koder_agent.harness.sandbox.backend import (
    SandboxBackendCapabilities,
    SandboxBackendStatus,
)
from koder_agent.harness.sandbox.policy import SandboxPolicy
from koder_agent.harness.tools.shell_executor import ShellExecutionResult
from koder_agent.tools.permission_context import (
    reset_tool_permission_context,
    set_tool_permission_context,
)


def _sandbox_state(tmp_path, *, status_reason: str = "available"):
    policy = SandboxPolicy.from_config(
        {
            "enabled": True,
            "mode": "read-only",
            "backend": "unix-local",
            "networkAccess": False,
            "writableRoots": ["build"],
            "allowRead": ["docs"],
            "denyRead": ["secrets"],
            "allowWrite": ["tmp"],
            "denyWrite": ["credentials.json"],
            "protectedPaths": [".git", "private"],
        }
    )
    capabilities = SandboxBackendCapabilities(
        supports_host_process_isolation="enforced",
        supports_workspace_isolation="enforced",
        supports_repository_sync="enforced",
        supports_read_only_filesystem="enforced",
        supports_network_policy="enforced",
        supports_domain_policy="enforced",
        supports_protected_paths="enforced",
    )
    status = SandboxBackendStatus(
        backend_id="unix-local",
        selected=True,
        available=True,
        reason=status_reason,
        capabilities=capabilities,
    )
    base = powershell.resolve_sandbox_settings(tmp_path)
    return base.__class__(
        **{
            **base.__dict__,
            "enabled": True,
            "policy": policy,
            "backend": "unix-local",
            "backend_available": True,
            "backend_statuses": (status,),
        }
    )


async def _invoke_with_two_approvals(monkeypatch, state, approver):
    service = MagicMock()
    service.evaluate_tool_call_async = AsyncMock(
        return_value=PermissionEvaluationResult.approval_required(
            tool_name="run_powershell",
            reason="generic PowerShell mutation approval",
            mode=PermissionMode.DEFAULT,
        )
    )
    monkeypatch.setattr(powershell, "resolve_sandbox_settings", lambda _cwd: state)
    monkeypatch.setattr(powershell, "is_excluded_command", lambda *_a, **_k: False)
    token = set_tool_permission_context(service, approver=approver)
    try:
        return await powershell.run_powershell.on_invoke_tool(
            None,
            json.dumps({"command": "New-Item artifact.txt"}),
        )
    finally:
        reset_tool_permission_context(token)


@pytest.mark.asyncio
async def test_powershell_second_approval_enumerates_every_exact_loss(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    state = _sandbox_state(tmp_path)
    approvals: list[str] = []
    executions: list[str] = []

    async def approver(_tool, _arguments, decision):
        approvals.append(decision.reason)
        return True

    async def fake_execute(command, **_kwargs):
        executions.append(command)
        return ShellExecutionResult(status="success", output="executed", exit_code=0)

    monkeypatch.setattr(powershell, "execute_powershell_command", fake_execute)
    result = await _invoke_with_two_approvals(monkeypatch, state, approver)

    assert len(approvals) == 2
    assert approvals[0] == "generic PowerShell mutation approval"
    exact = approvals[1]
    for expected in (
        "PowerShell sandbox execution is unsupported",
        "host process isolation",
        "workspace materialization and isolation",
        "repository synchronization",
        "read-only mode",
        "mode=read-only",
        "networkAccess=false",
        "writableRoots=['build']",
        "allowRead=['docs']",
        "denyRead=['secrets']",
        "allowWrite=['tmp']",
        "denyWrite=['credentials.json']",
        "protectedPaths=['.git', 'private']",
        "generic command or mutation approval does not accept these losses",
    ):
        assert expected in exact
    assert executions == ["New-Item artifact.txt"]
    assert "exact state-bound approval" in result


@pytest.mark.asyncio
async def test_powershell_second_approval_denial_prevents_execution(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    state = _sandbox_state(tmp_path)
    approvals: list[str] = []
    executions: list[str] = []

    async def approver(_tool, _arguments, decision):
        approvals.append(decision.reason)
        return len(approvals) == 1

    async def fake_execute(command, **_kwargs):
        executions.append(command)
        return ShellExecutionResult(status="success", output="must-not-run", exit_code=0)

    monkeypatch.setattr(powershell, "execute_powershell_command", fake_execute)
    result = await _invoke_with_two_approvals(monkeypatch, state, approver)

    assert len(approvals) == 2
    assert executions == []
    assert "executed: false" in result
    assert "exact PowerShell unsandboxed fallback approval was not granted" in result


@pytest.mark.asyncio
async def test_powershell_requirement_drift_after_second_approval_prevents_execution(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    state = _sandbox_state(tmp_path)
    changed_state = _sandbox_state(tmp_path, status_reason="changed after approval")
    approvals: list[str] = []
    executions: list[str] = []

    async def approver(_tool, _arguments, decision):
        approvals.append(decision.reason)
        if len(approvals) == 2:
            monkeypatch.setattr(
                powershell,
                "resolve_sandbox_settings",
                lambda _cwd: changed_state,
            )
        return True

    async def fake_execute(command, **_kwargs):
        executions.append(command)
        return ShellExecutionResult(status="success", output="must-not-run", exit_code=0)

    monkeypatch.setattr(powershell, "execute_powershell_command", fake_execute)
    result = await _invoke_with_two_approvals(monkeypatch, state, approver)

    assert len(approvals) == 2
    assert executions == []
    assert "requirement digest changed" in result
    assert "new exact approval is required" in result


@pytest.mark.asyncio
async def test_powershell_unchanged_state_executes_once(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    state = _sandbox_state(tmp_path)
    executions: list[str] = []

    async def approver(_tool, _arguments, _decision):
        return True

    async def fake_execute(command, **_kwargs):
        executions.append(command)
        return ShellExecutionResult(status="success", output="executed", exit_code=0)

    monkeypatch.setattr(powershell, "execute_powershell_command", fake_execute)
    result = await _invoke_with_two_approvals(monkeypatch, state, approver)

    assert executions == ["New-Item artifact.txt"]
    assert result.endswith("executed")
