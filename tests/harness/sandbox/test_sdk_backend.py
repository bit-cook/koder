import sys

import pytest

from koder_agent.harness.sandbox.backend import SandboxExecutionContext
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
    )

    result = await execute_with_sdk_backend(context)

    assert result.sandboxed is True
    assert result.backend_id == "unix-local"
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
        )
    )

    assert result.status == "policy_violation"
    assert "protected path" in (result.violation or "")
    assert not (tmp_path / ".git" / "config").exists()
