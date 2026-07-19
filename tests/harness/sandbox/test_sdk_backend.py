import asyncio
import sys
from types import SimpleNamespace

import pytest

from koder_agent.harness.sandbox.backend import (
    SandboxBackendCapabilities,
    SandboxExecutionContext,
)
from koder_agent.harness.sandbox.policy import SandboxPolicy
from koder_agent.harness.sandbox.registry import get_backend_status
from koder_agent.harness.sandbox.sdk_backend import execute_with_sdk_backend


@pytest.mark.asyncio
async def test_sdk_unix_local_executes_inside_workspace_and_blocks_escape(tmp_path):
    status = get_backend_status("unix-local")
    if not status.available:
        pytest.skip(status.reason)

    escape = tmp_path.parent / f"{tmp_path.name}-escape"
    if escape.exists():
        escape.unlink()
    policy = SandboxPolicy(mode="workspace-write", backend="unix-local")
    context = SandboxExecutionContext(
        cwd=tmp_path,
        repo_root=tmp_path,
        command=f"printf inside > inside.txt && printf escape > {escape}",
        env={},
        timeout=10,
        background=False,
        session_id=None,
        policy=policy,
        degradation_approved=True,
    )

    result = await execute_with_sdk_backend(context)

    assert result.sandboxed is True
    assert result.backend_id == "unix-local"
    if "sandbox_apply: Operation not permitted" in result.stderr:
        pytest.skip("nested macOS sandbox-exec is unavailable in this test environment")
    if sys.platform == "darwin":
        assert result.exit_code != 0
        assert not escape.exists()
    assert (tmp_path / "inside.txt").exists()


@pytest.mark.asyncio
async def test_sdk_backend_preflights_protected_path_writes(tmp_path):
    status = get_backend_status("unix-local")
    if not status.available:
        pytest.skip(status.reason)
    (tmp_path / ".git").mkdir()
    policy = SandboxPolicy(mode="workspace-write", backend="unix-local")

    result = await execute_with_sdk_backend(
        SandboxExecutionContext(
            cwd=tmp_path,
            repo_root=tmp_path,
            command="touch .git/config",
            env={},
            timeout=10,
            background=False,
            session_id=None,
            policy=policy,
            degradation_approved=True,
        )
    )

    assert result.status == "policy_violation"
    assert result.sandboxed is False
    assert result.created is False
    assert result.executed is False
    assert "protected path" in (result.violation or "")
    assert not (tmp_path / ".git" / "config").exists()


@pytest.mark.asyncio
async def test_create_failure_reports_sandboxed_false(tmp_path, monkeypatch):
    from koder_agent.harness.sandbox import sdk_backend

    class FailingClient:
        async def create(self, **_kwargs):
            raise RuntimeError("create failed before sandbox exists")

    status = SimpleNamespace(
        available=True,
        unavailable_reasons=(),
        reason="available",
        capabilities=SandboxBackendCapabilities(),
    )
    monkeypatch.setattr(sdk_backend, "select_backend_id", lambda _requested: "docker")
    monkeypatch.setattr(sdk_backend, "get_backend_status", lambda *_a, **_k: status)
    monkeypatch.setattr(
        sdk_backend,
        "create_backend_client_and_options",
        lambda *_a, **_k: (FailingClient(), object()),
    )

    result = await execute_with_sdk_backend(
        SandboxExecutionContext(
            cwd=tmp_path,
            repo_root=tmp_path,
            command="true",
            env={},
            timeout=10,
            background=False,
            session_id=None,
            policy=SandboxPolicy(
                mode="workspace-write",
                backend="docker",
                network_access=True,
            ),
            degradation_approved=True,
        )
    )

    assert result.sandboxed is False
    assert result.created is False
    assert result.executed is False
    assert "create failed before sandbox exists" in (result.reason or "")


def test_preflight_violation_reports_not_executed(tmp_path):
    policy = SandboxPolicy(mode="workspace-write", backend="unix-local")
    result = asyncio.run(
        execute_with_sdk_backend(
            SandboxExecutionContext(
                cwd=tmp_path,
                repo_root=tmp_path,
                command="touch .git/config",
                env={},
                timeout=10,
                background=False,
                session_id=None,
                policy=policy,
                degradation_approved=True,
            )
        )
    )

    if result.status == "unavailable":
        pytest.skip(result.reason)
    assert result.status == "policy_violation"
    assert result.sandboxed is False
    assert result.created is False
    assert result.executed is False


@pytest.mark.asyncio
async def test_cancellation_deletes_created_session(tmp_path, monkeypatch):
    from koder_agent.harness.sandbox import sdk_backend

    calls = []
    exec_started = asyncio.Event()
    release_exec = asyncio.Event()

    class BlockingSession:
        async def __aenter__(self):
            calls.append("enter")
            return self

        async def __aexit__(self, *_exc):
            calls.append("exit")

        async def exec(self, *_args, **_kwargs):
            calls.append("exec")
            exec_started.set()
            await release_exec.wait()

    class FakeClient:
        async def create(self, **_kwargs):
            calls.append("create")
            return BlockingSession()

        async def delete(self, _session):
            calls.append("delete")

    status = SimpleNamespace(
        available=True,
        unavailable_reasons=(),
        reason="available",
        capabilities=SandboxBackendCapabilities(),
    )
    monkeypatch.setattr(sdk_backend, "select_backend_id", lambda _requested: "docker")
    monkeypatch.setattr(sdk_backend, "get_backend_status", lambda *_a, **_k: status)
    monkeypatch.setattr(
        sdk_backend,
        "create_backend_client_and_options",
        lambda *_a, **_k: (FakeClient(), object()),
    )

    task = asyncio.create_task(
        execute_with_sdk_backend(
            SandboxExecutionContext(
                cwd=tmp_path,
                repo_root=tmp_path,
                command="blocked",
                env={},
                timeout=10,
                background=False,
                session_id=None,
                policy=SandboxPolicy(
                    mode="workspace-write",
                    backend="docker",
                    network_access=True,
                ),
                degradation_approved=True,
            )
        )
    )
    await exec_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls == ["create", "enter", "exec", "exit", "delete"]
