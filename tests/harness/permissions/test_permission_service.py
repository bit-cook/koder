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
from koder_agent.harness.sandbox.backend import (
    SandboxBackendCapabilities,
    SandboxBackendStatus,
)
from koder_agent.harness.sandbox.policy import SandboxPolicy
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
    assert result.requires_approval is True
    assert "sandbox backend is unavailable" in result.reason
    assert "second explicit approval" in result.reason


def test_permission_service_unix_local_requires_approval_without_host_isolation(
    tmp_path, monkeypatch
):
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
                    "networkAccess": True,
                    "autoAllowBashIfSandboxed": True,
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


def _sandbox_state(
    *,
    policy: SandboxPolicy,
    network_capability: str,
    domain_capability: str = "unsupported",
):
    backend_status = SandboxBackendStatus(
        backend_id=policy.backend,
        selected=True,
        available=True,
        reason="available",
        capabilities=SandboxBackendCapabilities(
            supports_host_process_isolation="enforced",
            supports_workspace_isolation="enforced",
            supports_repository_sync="enforced",
            supports_read_only_filesystem="enforced",
            supports_network_policy=network_capability,
            supports_domain_policy=domain_capability,
            supports_protected_paths="enforced",
        ),
    )
    return types.SimpleNamespace(
        enabled=True,
        backend=policy.backend,
        backend_available=True,
        backend_reason="available",
        platform_enabled=True,
        auto_allow_bash_if_sandboxed=True,
        policy=policy,
        backend_statuses=(backend_status,),
    )


@pytest.mark.parametrize(
    ("tool_name", "command"),
    [
        ("run_shell", "curl https://example.com -o artifact.tar.gz"),
        ("git_command", "push origin main"),
    ],
)
def test_permission_service_does_not_auto_allow_unenforced_network_restriction(
    monkeypatch, tool_name, command
):
    policy = SandboxPolicy(
        mode="workspace-write",
        backend="unix-local",
        network_access=False,
    )
    state = _sandbox_state(policy=policy, network_capability="unsupported")
    monkeypatch.setattr(
        "koder_agent.harness.permissions.service.resolve_sandbox_settings",
        lambda _cwd: state,
    )
    monkeypatch.setattr(
        "koder_agent.harness.permissions.service.is_excluded_command",
        lambda _command, *, cwd: False,
    )

    result = PermissionService.default().evaluate_tool_call(tool_name, {"command": command})

    assert result.allowed is False
    assert result.requires_approval is True
    assert "network access disabled" in result.reason
    assert "networkAccess=false" in result.reason
    assert "generic command or mutation approval does not accept" in result.reason


@pytest.mark.parametrize(
    ("policy", "capabilities", "expected_losses"),
    (
        (
            SandboxPolicy.from_config(
                {
                    "enabled": True,
                    "backend": "unix-local",
                    "networkAccess": True,
                    "protectedPaths": ["private"],
                }
            ),
            SandboxBackendCapabilities(
                supports_host_process_isolation="enforced",
                supports_workspace_isolation="enforced",
                supports_repository_sync="enforced",
                supports_network_policy="enforced",
                supports_protected_paths="unsupported",
            ),
            ("protectedPaths", "protected subpaths"),
        ),
        (
            SandboxPolicy.from_config({"enabled": True, "backend": "e2b", "networkAccess": True}),
            SandboxBackendCapabilities(
                supports_host_process_isolation="remote-sandbox",
                supports_workspace_isolation="not-proven",
                supports_repository_sync="unsupported",
                supports_network_policy="enforced",
            ),
            (
                "workspace materialization and isolation",
                "repository synchronization",
            ),
        ),
    ),
)
def test_permission_service_names_incomplete_sandbox_guarantees(
    monkeypatch, policy, capabilities, expected_losses
):
    state = _sandbox_state(policy=policy, network_capability="enforced")
    state.backend_statuses = (
        SandboxBackendStatus(
            backend_id=policy.backend,
            selected=True,
            available=True,
            reason="available",
            capabilities=capabilities,
        ),
    )
    monkeypatch.setattr(
        "koder_agent.harness.permissions.service.resolve_sandbox_settings",
        lambda _cwd: state,
    )
    monkeypatch.setattr(
        "koder_agent.harness.permissions.service.is_excluded_command",
        lambda _command, *, cwd: False,
    )

    result = PermissionService.default().evaluate_tool_call(
        "run_shell",
        {"command": "python mutate.py"},
    )

    assert result.requires_approval is True
    for expected in expected_losses:
        assert expected in result.reason


def test_permission_service_auto_allows_only_with_complete_backend_capabilities(monkeypatch):
    policy = SandboxPolicy(
        mode="workspace-write",
        backend="unix-local",
        network_access=True,
    )
    state = _sandbox_state(policy=policy, network_capability="unsupported")
    monkeypatch.setattr(
        "koder_agent.harness.permissions.service.resolve_sandbox_settings",
        lambda _cwd: state,
    )
    monkeypatch.setattr(
        "koder_agent.harness.permissions.service.is_excluded_command",
        lambda _command, *, cwd: False,
    )

    result = PermissionService.default().evaluate_tool_call(
        "run_shell",
        {"command": "touch artifact.tar.gz"},
    )

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
