"""End-to-end coverage for model-facing shell sandbox execution."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from koder_agent.harness.permissions.service import PermissionService
from koder_agent.harness.sandbox.backend import (
    SandboxBackendCapabilities,
    SandboxBackendStatus,
    SandboxExecutionResult,
)
from koder_agent.harness.sandbox.policy import SandboxPolicy
from koder_agent.tools import get_all_tools
from koder_agent.tools.permission_context import (
    reset_tool_permission_context,
    set_tool_permission_context,
)


def _model_tool(name: str):
    return next(tool for tool in get_all_tools() if getattr(tool, "name", None) == name)


def _sandbox_state(
    policy: SandboxPolicy,
    *,
    capabilities: SandboxBackendCapabilities,
    enabled: bool = True,
    available: bool = True,
):
    status = SandboxBackendStatus(
        backend_id=policy.backend,
        selected=True,
        available=available,
        reason="available" if available else "backend unavailable",
        capabilities=capabilities,
    )
    return SimpleNamespace(
        enabled=enabled,
        backend=policy.backend,
        backend_available=available,
        backend_reason=status.reason,
        platform_enabled=True,
        auto_allow_bash_if_sandboxed=True,
        policy=policy if enabled else None,
        backend_statuses=(status,),
    )


async def _invoke_with_permissions(tool_name: str, arguments: dict, *, approver=None) -> str:
    token = set_tool_permission_context(PermissionService.default(), approver=approver)
    try:
        return await _model_tool(tool_name).on_invoke_tool(None, json.dumps(arguments))
    finally:
        reset_tool_permission_context(token)


def _patch_states(monkeypatch, permission_state, execution_state=None):
    from koder_agent.harness.permissions import service
    from koder_agent.harness.tools import shell_executor

    execution_state = execution_state or permission_state
    monkeypatch.setattr(service, "resolve_sandbox_settings", lambda _cwd: permission_state)
    monkeypatch.setattr(service, "is_excluded_command", lambda *_a, **_k: False)
    monkeypatch.setattr(shell_executor, "resolve_sandbox_settings", lambda _cwd: execution_state)
    monkeypatch.setattr(shell_executor, "is_excluded_command", lambda *_a, **_k: False)
    return shell_executor


def _enforcing_capabilities(*, network: str = "unsupported", domains: str = "unsupported"):
    return SandboxBackendCapabilities(
        supports_host_process_isolation="enforced",
        supports_workspace_isolation="enforced",
        supports_repository_sync="enforced",
        supports_read_only_filesystem="enforced",
        supports_network_policy=network,
        supports_domain_policy=domains,
        supports_protected_paths="enforced",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "executed_command"),
    (
        ("run_shell", {"command": "touch artifact.txt"}, "touch artifact.txt"),
        ("git_command", {"command": "push origin main"}, "git push origin main"),
    ),
)
async def test_model_function_tools_execute_autoapproved_calls_in_sandbox(
    monkeypatch, tool_name, arguments, executed_command
):
    policy = SandboxPolicy.from_config(
        {
            "enabled": True,
            "backend": "unix-local",
            "networkAccess": True,
        }
    )
    state = _sandbox_state(policy, capabilities=_enforcing_capabilities())
    shell_executor = _patch_states(monkeypatch, state)
    calls = []

    async def fake_backend(context):
        calls.append(context)
        return SandboxExecutionResult(
            status="success",
            stdout="sandbox-ok",
            exit_code=0,
            backend_id=policy.backend,
            sandboxed=True,
            created=True,
            executed=True,
        )

    async def host_execution_forbidden(*_args, **_kwargs):
        raise AssertionError("auto-approved model command reached host execution")

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", fake_backend)
    monkeypatch.setattr(
        shell_executor,
        "_run_foreground_unsandboxed",
        host_execution_forbidden,
    )

    output = await _invoke_with_permissions(tool_name, arguments)

    assert len(calls) == 1
    assert calls[0].command == executed_command
    assert calls[0].policy.backend == "unix-local"
    assert "sandboxed: true" in output
    assert "backend: unix-local" in output
    assert "sandbox-ok" in output


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ("disabled", "policy", "capability", "cwd"))
async def test_autoapproved_invocation_blocks_host_when_execution_state_changes(
    monkeypatch, drift, tmp_path
):
    policy = SandboxPolicy.from_config(
        {
            "enabled": True,
            "backend": "unix-local",
            "networkAccess": True,
        }
    )
    permission_state = _sandbox_state(policy, capabilities=_enforcing_capabilities())
    execution_state = permission_state
    if drift == "disabled":
        execution_state = _sandbox_state(
            SandboxPolicy.from_config({"enabled": False}),
            capabilities=_enforcing_capabilities(),
            enabled=False,
            available=False,
        )
    elif drift == "policy":
        execution_state = _sandbox_state(
            SandboxPolicy.from_config(
                {
                    "enabled": True,
                    "backend": "unix-local",
                    "networkAccess": False,
                }
            ),
            capabilities=_enforcing_capabilities(network="enforced"),
        )
    elif drift == "capability":
        execution_state = _sandbox_state(
            policy,
            capabilities=SandboxBackendCapabilities(
                supports_host_process_isolation="enforced",
                supports_workspace_isolation="enforced",
                supports_repository_sync="enforced",
                supports_read_only_filesystem="enforced",
                supports_protected_paths="enforced",
                supports_network_policy="changed-after-approval",
            ),
        )
    shell_executor = _patch_states(monkeypatch, permission_state, execution_state)
    if drift == "cwd":
        from koder_agent.harness.permissions import service

        cwd_a = tmp_path / "a"
        cwd_b = tmp_path / "b"
        cwd_a.mkdir()
        cwd_b.mkdir()
        monkeypatch.setattr(service, "canonical_workspace_path", lambda _cwd: str(cwd_a))
        monkeypatch.setattr(
            shell_executor,
            "canonical_workspace_path",
            lambda _cwd: str(cwd_b),
        )

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("host or backend execution should not occur after state mismatch")

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", forbidden)
    monkeypatch.setattr(shell_executor, "_run_foreground_unsandboxed", forbidden)

    output = await _invoke_with_permissions("run_shell", {"command": "touch artifact.txt"})

    assert "sandboxed: false" in output
    assert "created: false" in output
    assert "executed: false" in output
    assert "host execution was blocked" in output
    expected = {
        "disabled": "sandbox is no longer enabled",
        "policy": "sandbox policy changed",
        "capability": "sandbox backend capabilities changed",
        "cwd": "canonical cwd changed",
    }[drift]
    assert expected in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_field", "value"),
    (
        ("writableRoots", ["src"]),
        ("allowRead", ["src"]),
        ("denyRead", ["secret.txt"]),
        ("allowWrite", ["src"]),
        ("denyWrite", ["secret.txt"]),
        ("protectedPaths", ["private"]),
    ),
)
async def test_model_tool_requires_normal_approval_for_unenforced_custom_policy(
    monkeypatch, config_field, value
):
    policy = SandboxPolicy.from_config(
        {
            "enabled": True,
            "backend": "unix-local",
            "networkAccess": True,
            config_field: value,
        }
    )
    capabilities = _enforcing_capabilities()
    if config_field in {"denyWrite", "protectedPaths"}:
        capabilities = SandboxBackendCapabilities(
            **{
                **capabilities.__dict__,
                "supports_protected_paths": "unsupported",
            }
        )
    state = _sandbox_state(policy, capabilities=capabilities)
    shell_executor = _patch_states(monkeypatch, state)
    called = False

    async def forbidden(_context):
        nonlocal called
        called = True
        raise AssertionError("approval-gated command reached sandbox executor")

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", forbidden)
    monkeypatch.setenv("KODER_ENFORCE_TOOL_APPROVAL", "1")

    output = await _invoke_with_permissions("run_shell", {"command": "touch artifact.txt"})

    assert output.startswith("Permission denied for run_shell:")
    assert "No approver is available" in output
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("backend", "network_access", "allowed_domains", "capabilities", "autoapproved"),
    (
        (
            "unix-local",
            True,
            (),
            _enforcing_capabilities(network="unsupported"),
            True,
        ),
        (
            "unix-local",
            False,
            (),
            _enforcing_capabilities(network="unsupported"),
            False,
        ),
        (
            "e2b",
            False,
            (),
            _enforcing_capabilities(network="enforced"),
            True,
        ),
        (
            "e2b",
            True,
            ("example.com",),
            _enforcing_capabilities(network="enforced", domains="unsupported"),
            False,
        ),
    ),
)
async def test_model_tool_network_policy_truth_controls_autoapproval(
    monkeypatch,
    backend,
    network_access,
    allowed_domains,
    capabilities,
    autoapproved,
):
    config = {
        "enabled": True,
        "backend": backend,
        "networkAccess": network_access,
    }
    if allowed_domains:
        config["allowedDomains"] = list(allowed_domains)
    policy = SandboxPolicy.from_config(config)
    state = _sandbox_state(policy, capabilities=capabilities)
    shell_executor = _patch_states(monkeypatch, state)
    calls = []

    async def fake_backend(context):
        calls.append(context)
        return SandboxExecutionResult(
            status="success",
            stdout="ok",
            exit_code=0,
            backend_id=backend,
            sandboxed=True,
            created=True,
            executed=True,
        )

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", fake_backend)
    monkeypatch.setenv("KODER_ENFORCE_TOOL_APPROVAL", "1")

    output = await _invoke_with_permissions("run_shell", {"command": "touch artifact.txt"})

    if autoapproved:
        assert len(calls) == 1
        assert "sandboxed: true" in output
    else:
        assert calls == []
        assert output.startswith("Permission denied for run_shell:")
        assert "No approver is available" in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy_config", "capabilities", "expected_losses"),
    (
        (
            {
                "enabled": True,
                "backend": "unix-local",
                "networkAccess": False,
            },
            _enforcing_capabilities(network="unsupported"),
            ("network access disabled", "networkAccess=false"),
        ),
        (
            {
                "enabled": True,
                "backend": "unix-local",
                "networkAccess": True,
                "protectedPaths": ["private"],
            },
            SandboxBackendCapabilities(
                **{
                    **_enforcing_capabilities(network="enforced").__dict__,
                    "supports_protected_paths": "unsupported",
                }
            ),
            ("protectedPaths", "protected subpaths"),
        ),
        (
            {
                "enabled": True,
                "backend": "e2b",
                "networkAccess": True,
            },
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
async def test_generic_mutation_approval_cannot_accept_sandbox_degradation(
    monkeypatch,
    policy_config,
    capabilities,
    expected_losses,
):
    policy = SandboxPolicy.from_config(policy_config)
    state = _sandbox_state(policy, capabilities=capabilities)
    shell_executor = _patch_states(monkeypatch, state)
    approvals: list[str] = []

    async def approver(_tool_name, _arguments, decision):
        approvals.append(decision.reason)
        return "allow" if len(approvals) == 1 else "deny"

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("backend execution occurred without degradation approval")

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", forbidden)

    output = await _invoke_with_permissions(
        "run_shell",
        {"command": "python mutate.py"},
        approver=approver,
    )

    assert len(approvals) == 2
    assert "generic command or mutation approval does not accept" in approvals[0]
    assert "sandbox degradation approval required before execution" in approvals[1]
    for expected in expected_losses:
        assert expected in approvals[1]
    assert "executed: false" in output
    assert "explicit sandbox degradation approval required" in output


@pytest.mark.asyncio
async def test_hosted_repository_command_runs_only_after_exact_loss_approval(monkeypatch):
    policy = SandboxPolicy.from_config({"enabled": True, "backend": "e2b", "networkAccess": True})
    capabilities = SandboxBackendCapabilities(
        supports_host_process_isolation="remote-sandbox",
        supports_workspace_isolation="not-proven",
        supports_repository_sync="unsupported",
        supports_network_policy="enforced",
    )
    state = _sandbox_state(policy, capabilities=capabilities)
    shell_executor = _patch_states(monkeypatch, state)
    approvals: list[str] = []
    calls = []

    async def approver(_tool_name, _arguments, decision):
        approvals.append(decision.reason)
        return "allow"

    async def fake_backend(context):
        calls.append(context)
        return SandboxExecutionResult(
            status="success",
            stdout="repo-command-ran",
            exit_code=0,
            backend_id="e2b",
            sandboxed=True,
            created=True,
            executed=True,
        )

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", fake_backend)

    output = await _invoke_with_permissions(
        "run_shell",
        {"command": "git status"},
        approver=approver,
    )

    assert len(approvals) == 1
    assert "workspace materialization and isolation" in approvals[0]
    assert "repository synchronization" in approvals[0]
    assert len(calls) == 1
    assert "repo-command-ran" in output


@pytest.mark.asyncio
async def test_model_tool_reports_sandbox_backend_errors(monkeypatch):
    policy = SandboxPolicy.from_config(
        {
            "enabled": True,
            "backend": "unix-local",
            "networkAccess": True,
        }
    )
    state = _sandbox_state(policy, capabilities=_enforcing_capabilities())
    shell_executor = _patch_states(monkeypatch, state)

    async def failing_backend(_context):
        return SandboxExecutionResult(
            status="error",
            backend_id="unix-local",
            sandboxed=True,
            created=True,
            executed=True,
            reason="backend exploded",
        )

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", failing_backend)

    output = await _invoke_with_permissions("run_shell", {"command": "touch artifact.txt"})

    assert "sandboxed: true" in output
    assert "backend: unix-local" in output
    assert "sandbox error: backend exploded" in output


@pytest.mark.asyncio
async def test_unix_local_does_not_autoapprove_without_host_process_isolation(
    monkeypatch,
):
    policy = SandboxPolicy.from_config(
        {"enabled": True, "backend": "unix-local", "networkAccess": True}
    )
    capabilities = _enforcing_capabilities()
    capabilities = SandboxBackendCapabilities(
        **{
            **capabilities.__dict__,
            "supports_host_process_isolation": "unsupported",
        }
    )
    state = _sandbox_state(policy, capabilities=capabilities)
    shell_executor = _patch_states(monkeypatch, state)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("parent-process visibility probe must not auto-execute")

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", forbidden)
    monkeypatch.setattr(shell_executor, "_run_foreground_unsandboxed", forbidden)
    monkeypatch.setenv("KODER_ENFORCE_TOOL_APPROVAL", "1")

    output = await _invoke_with_permissions(
        "run_shell",
        {"command": f"kill -0 {os.getppid()} && ps -p {os.getppid()}"},
    )

    assert output.startswith("Permission denied for run_shell:")
    assert "No approver is available" in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "command"),
    (
        ("run_shell", "echo $(./mutate.sh)"),
        ("run_shell", "cat mutate.sh | sh"),
        ("run_shell", "(./mutate.sh)"),
        ("run_shell", "python3 mutate.py"),
        ("run_shell", "git log --output=.git/pwned"),
        ("git_command", "log --output=.git/pwned"),
    ),
)
async def test_hidden_execution_forms_remain_approval_gated(
    monkeypatch, tmp_path, tool_name, command
):
    (tmp_path / ".git").mkdir()
    sentinel = tmp_path / ".git" / "sentinel"
    sentinel.write_text("unchanged", encoding="utf-8")
    (tmp_path / "mutate.sh").write_text("touch .git/pwned\n", encoding="utf-8")
    (tmp_path / "mutate.py").write_text(
        "from pathlib import Path\nPath('.git/pwned').touch()\n",
        encoding="utf-8",
    )
    (tmp_path / "mutate.sh").chmod(0o755)
    monkeypatch.chdir(tmp_path)
    policy = SandboxPolicy.from_config(
        {"enabled": True, "backend": "unix-local", "networkAccess": True}
    )
    capabilities = SandboxBackendCapabilities(
        supports_host_process_isolation="unsupported",
        supports_workspace_isolation="not-proven",
        supports_repository_sync="not-applicable-host-workspace",
        supports_protected_paths="unsupported",
    )
    state = _sandbox_state(policy, capabilities=capabilities)
    shell_executor = _patch_states(monkeypatch, state)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("approval-gated command reached an execution path")

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", forbidden)
    monkeypatch.setattr(shell_executor, "_run_foreground_unsandboxed", forbidden)
    monkeypatch.setenv("KODER_ENFORCE_TOOL_APPROVAL", "1")

    output = await _invoke_with_permissions(tool_name, {"command": command})

    assert output.startswith(f"Permission denied for {tool_name}:")
    assert not (tmp_path / ".git" / "pwned").exists()
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.asyncio
async def test_model_tool_reprompts_before_unavailable_backend_fallback(monkeypatch):
    from koder_agent.harness.tools.shell_executor import ShellExecutionResult

    policy = SandboxPolicy.from_config(
        {"enabled": True, "backend": "docker", "networkAccess": True}
    )
    state = _sandbox_state(
        policy,
        capabilities=SandboxBackendCapabilities(),
        available=False,
    )
    shell_executor = _patch_states(monkeypatch, state)
    approvals = []
    host_calls = []

    async def approver(_tool_name, _arguments, decision):
        approvals.append(decision.reason)
        return "allow"

    async def unavailable_backend(_context):
        return SandboxExecutionResult(
            status="unavailable",
            backend_id="docker",
            sandboxed=False,
            reason="docker daemon unavailable",
        )

    async def host_execution(command, **kwargs):
        host_calls.append((command, kwargs.get("warning")))
        return ShellExecutionResult(
            status="success",
            output=f"{kwargs.get('warning')}\nhost-ok",
            exit_code=0,
        )

    monkeypatch.setattr(shell_executor, "execute_with_sdk_backend", unavailable_backend)
    monkeypatch.setattr(shell_executor, "_run_foreground_unsandboxed", host_execution)

    output = await _invoke_with_permissions(
        "run_shell",
        {"command": "touch artifact.txt"},
        approver=approver,
    )

    assert len(approvals) == 2
    assert "second explicit approval" in approvals[0]
    assert "host process isolation" in approvals[1]
    assert "workspace materialization and isolation" in approvals[1]
    assert "repository synchronization" in approvals[1]
    assert host_calls == [
        (
            "touch artifact.txt",
            "warning: sandbox unavailable (docker daemon unavailable); "
            "running command UNSANDBOXED with exact state-bound approval",
        )
    ]
    assert "UNSANDBOXED with exact state-bound approval" in output
    assert "host-ok" in output
