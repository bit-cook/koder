# ruff: noqa: E402
"""Graceful-degradation tests for the sandbox init-failure path.

When the sandbox backend reports ``unavailable``/``unsupported``,
``execute_shell_command`` must:

- keep the fail-closed error when no approval callback is supplied (safe default),
- keep the error when an approval callback denies,
- fall through to a real UNSANDBOXED foreground run, with a visible warning,
  only when an approval callback approves,
- leave the sandboxed path untouched when the backend IS available.
"""

import asyncio
import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues.
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import koder_agent.harness.tools.shell_executor as se
from koder_agent.harness.sandbox.backend import SandboxExecutionResult
from koder_agent.harness.sandbox.policy import SandboxPolicy
from koder_agent.tools.shell import build_sandbox_unavailable_approval


def _force_sandbox_state(monkeypatch, tmp_path, *, backend_result: SandboxExecutionResult):
    """Force the sandboxed branch and stub the backend to a fixed result.

    Fabricates an enabled sandbox state carrying a policy, stubs the
    excluded-command check to False, and replaces the SDK backend with a fake
    that returns ``backend_result``. Returns the fake backend's call log.
    """
    policy = SandboxPolicy.from_config({"enabled": True, "mode": "workspace-write"})
    real_state = se.resolve_sandbox_settings(str(tmp_path))
    forced = real_state.__class__(
        **{**real_state.__dict__, "enabled": True, "policy": policy, "backend": "unix-local"}
    )
    monkeypatch.setattr(se, "resolve_sandbox_settings", lambda *_a, **_k: forced)
    monkeypatch.setattr(se, "is_excluded_command", lambda *_a, **_k: False)

    calls: list[dict] = []

    async def _fake_backend(ctx):
        calls.append({"command": ctx.command})
        return backend_result

    monkeypatch.setattr(se, "execute_with_sdk_backend", _fake_backend)
    return calls


# --- unavailable + approval granted -> runs UNSANDBOXED with a warning --------


def test_unavailable_with_approval_runs_unsandboxed_with_warning(monkeypatch, tmp_path):
    _force_sandbox_state(
        monkeypatch,
        tmp_path,
        backend_result=SandboxExecutionResult(status="unavailable", reason="docker not found"),
    )
    monkeypatch.chdir(tmp_path)

    result = asyncio.run(
        se.execute_shell_command(
            "echo degraded-ok",
            timeout=10,
            sandbox_unavailable_approval=lambda reason: True,
        )
    )

    # The command actually ran unsandboxed.
    assert result.status == "success"
    assert result.exit_code == 0
    assert "degraded-ok" in result.output
    # A one-line warning made the degradation visible, including the reason.
    assert "warning: sandbox unavailable" in result.output
    assert "docker not found" in result.output
    assert "UNSANDBOXED" in result.output


def test_unsupported_with_async_approval_runs_unsandboxed(monkeypatch, tmp_path):
    """Async approval callbacks are awaited; 'unsupported' also degrades."""
    _force_sandbox_state(
        monkeypatch,
        tmp_path,
        backend_result=SandboxExecutionResult(status="unsupported", reason="non-darwin unix-local"),
    )
    monkeypatch.chdir(tmp_path)

    async def _approve(reason: str) -> bool:
        assert "unix-local" in reason
        return True

    result = asyncio.run(
        se.execute_shell_command(
            "echo async-ok",
            timeout=10,
            sandbox_unavailable_approval=_approve,
        )
    )

    assert result.status == "success"
    assert "async-ok" in result.output
    assert "warning: sandbox unavailable" in result.output


# --- unavailable + approval denied -> keeps the fail-closed error -------------


def test_unavailable_with_denied_approval_errors(monkeypatch, tmp_path):
    _force_sandbox_state(
        monkeypatch,
        tmp_path,
        backend_result=SandboxExecutionResult(status="unavailable", reason="docker not found"),
    )
    monkeypatch.chdir(tmp_path)

    result = asyncio.run(
        se.execute_shell_command(
            "echo should-not-run",
            timeout=10,
            sandbox_unavailable_approval=lambda reason: False,
        )
    )

    assert result.status == "error"
    assert "sandboxed: false" in result.output
    assert "docker not found" in result.output
    # The command did not run: its output is absent from the error.
    assert "should-not-run" not in result.output


def test_unavailable_without_callback_is_fail_closed(monkeypatch, tmp_path):
    """Default (no callback) must remain the safe fail-closed error."""
    _force_sandbox_state(
        monkeypatch,
        tmp_path,
        backend_result=SandboxExecutionResult(status="unavailable", reason="docker not found"),
    )
    monkeypatch.chdir(tmp_path)

    result = asyncio.run(se.execute_shell_command("echo nope", timeout=10))

    assert result.status == "error"
    assert "sandboxed: false" in result.output
    assert "nope" not in result.output


def test_raising_approval_callback_is_treated_as_denial(monkeypatch, tmp_path):
    """A callback that raises must not silently run unsandboxed."""
    _force_sandbox_state(
        monkeypatch,
        tmp_path,
        backend_result=SandboxExecutionResult(status="unavailable", reason="docker not found"),
    )
    monkeypatch.chdir(tmp_path)

    def _boom(reason: str) -> bool:
        raise RuntimeError("approval prompt blew up")

    result = asyncio.run(
        se.execute_shell_command(
            "echo boom",
            timeout=10,
            sandbox_unavailable_approval=_boom,
        )
    )

    assert result.status == "error"
    assert "sandboxed: false" in result.output
    assert "boom" not in result.output


# --- non-regression: sandbox available -> path unchanged, callback ignored ----


def test_available_sandbox_path_unchanged_and_callback_not_consulted(monkeypatch, tmp_path):
    calls = _force_sandbox_state(
        monkeypatch,
        tmp_path,
        backend_result=SandboxExecutionResult(
            status="ok",
            exit_code=0,
            stdout="hello",
            stderr="",
            sandboxed=True,
            backend_id="unix-local",
        ),
    )
    monkeypatch.chdir(tmp_path)

    approval_consulted = {"called": False}

    def _approval(reason: str) -> bool:
        approval_consulted["called"] = True
        return True

    result = asyncio.run(
        se.execute_shell_command(
            "echo hello",
            timeout=10,
            sandbox_unavailable_approval=_approval,
        )
    )

    # Sandboxed path taken as before.
    assert result.status == "success"
    assert result.exit_code == 0
    assert "sandboxed: true" in result.output
    assert "backend: unix-local" in result.output
    # No degradation, so no warning and the approval callback was never asked.
    assert "warning: sandbox unavailable" not in result.output
    assert approval_consulted["called"] is False
    assert len(calls) == 1


def test_no_sandbox_foreground_path_output_is_unchanged(monkeypatch, tmp_path):
    """When sandbox is disabled the normal foreground output has no warning."""
    real_state = se.resolve_sandbox_settings(str(tmp_path))
    disabled = real_state.__class__(**{**real_state.__dict__, "enabled": False, "policy": None})
    monkeypatch.setattr(se, "resolve_sandbox_settings", lambda *_a, **_k: disabled)
    monkeypatch.chdir(tmp_path)

    result = asyncio.run(
        se.execute_shell_command(
            "echo plain",
            timeout=10,
            sandbox_unavailable_approval=lambda reason: True,
        )
    )

    assert result.status == "success"
    assert result.output == "plain"
    assert "warning" not in result.output


# --- the tool/permission-layer factory ----------------------------------------


def test_build_sandbox_unavailable_approval_defaults_to_deny():
    approval = build_sandbox_unavailable_approval()
    assert asyncio.run(approval("docker missing")) is False


def test_build_sandbox_unavailable_approval_delegates_to_approver():
    seen: list[str] = []

    def _approver(reason: str) -> bool:
        seen.append(reason)
        return True

    approval = build_sandbox_unavailable_approval(_approver)
    assert asyncio.run(approval("docker missing")) is True
    assert seen == ["docker missing"]


def test_build_sandbox_unavailable_approval_awaits_async_approver():
    async def _approver(reason: str) -> bool:
        return "unix-local" in reason

    approval = build_sandbox_unavailable_approval(_approver)
    assert asyncio.run(approval("non-darwin unix-local")) is True
    assert asyncio.run(approval("docker missing")) is False


def test_build_sandbox_unavailable_approval_threads_into_executor(monkeypatch, tmp_path):
    """End-to-end: the factory's callback drives real degradation."""
    _force_sandbox_state(
        monkeypatch,
        tmp_path,
        backend_result=SandboxExecutionResult(status="unavailable", reason="docker not found"),
    )
    monkeypatch.chdir(tmp_path)

    approval = build_sandbox_unavailable_approval(lambda reason: True)
    result = asyncio.run(
        se.execute_shell_command(
            "echo factory-run",
            timeout=10,
            sandbox_unavailable_approval=approval,
        )
    )

    assert result.status == "success"
    assert "factory-run" in result.output
    assert "warning: sandbox unavailable" in result.output
