"""No-model sandbox backend smoke checks."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from koder_agent.harness.session_env import build_subprocess_env

from .backend import SandboxExecutionContext
from .policy import SandboxPolicy
from .registry import get_backend_status
from .sdk_backend import execute_with_sdk_backend


@dataclass(frozen=True)
class SandboxSmokeResult:
    backend_id: str
    passed: bool
    skipped: bool = False
    reason: str | None = None
    checks: tuple[str, ...] = field(default_factory=tuple)


async def run_backend_smoke_async(
    backend_id: str,
    *,
    case: str = "default",
    skip_unavailable: bool = False,
) -> SandboxSmokeResult:
    status = get_backend_status(backend_id, selected=True)
    if not status.available:
        reason = "; ".join(status.unavailable_reasons) or status.reason
        return SandboxSmokeResult(
            backend_id=backend_id,
            passed=False,
            skipped=skip_unavailable,
            reason=reason,
        )

    workspace = Path(tempfile.mkdtemp(prefix="koder-sandbox-smoke-"))
    escape = workspace.parent / f"{workspace.name}-escape"
    checks: list[str] = []
    try:
        policy = SandboxPolicy(
            mode="workspace-write",
            backend=backend_id,
        )

        async def run(command: str):
            return await execute_with_sdk_backend(
                SandboxExecutionContext(
                    cwd=workspace,
                    repo_root=workspace,
                    command=command,
                    env=build_subprocess_env(None),
                    timeout=10,
                    background=False,
                    session_id=None,
                    policy=policy,
                )
            )

        pwd = await run("pwd")
        if pwd.exit_code != 0 or str(workspace) not in pwd.combined_output():
            return SandboxSmokeResult(
                backend_id=backend_id,
                passed=False,
                reason="pwd did not execute inside workspace",
                checks=tuple(checks),
            )
        checks.append("pwd")

        inside = await run("printf inside > inside.txt && cat inside.txt")
        if inside.exit_code != 0 or "inside" not in inside.combined_output():
            return SandboxSmokeResult(
                backend_id=backend_id,
                passed=False,
                reason="workspace write failed",
                checks=tuple(checks),
            )
        checks.append("workspace-write")

        if case in {"default", "escape"}:
            if escape.exists():
                escape.unlink()
            outside = await run(f"printf escape > {escape}")
            if outside.exit_code == 0 or escape.exists():
                return SandboxSmokeResult(
                    backend_id=backend_id,
                    passed=False,
                    reason="outside workspace write was not blocked",
                    checks=tuple(checks),
                )
            checks.append("escape-blocked")

        if case == "protected-paths":
            protected = await run("mkdir -p .git && touch .git/sandbox-smoke")
            if protected.status != "policy_violation":
                return SandboxSmokeResult(
                    backend_id=backend_id,
                    passed=False,
                    reason="protected metadata write was not blocked",
                    checks=tuple(checks),
                )
            checks.append("protected-paths")

        if case == "network-deny":
            return SandboxSmokeResult(
                backend_id=backend_id,
                passed=False,
                skipped=True,
                reason="network domain policy is policy-only for this backend",
                checks=tuple(checks),
            )

        timeout = await execute_with_sdk_backend(
            SandboxExecutionContext(
                cwd=workspace,
                repo_root=workspace,
                command="sleep 2",
                env=build_subprocess_env(None),
                timeout=1,
                background=False,
                session_id=None,
                policy=policy,
            )
        )
        if timeout.status != "error":
            return SandboxSmokeResult(
                backend_id=backend_id,
                passed=False,
                reason="timeout behavior did not produce an error",
                checks=tuple(checks),
            )
        checks.append("timeout")

        return SandboxSmokeResult(backend_id=backend_id, passed=True, checks=tuple(checks))
    finally:
        if escape.exists():
            escape.unlink()
        shutil.rmtree(workspace, ignore_errors=True)


def run_backend_smoke(
    backend_id: str,
    *,
    case: str = "default",
    skip_unavailable: bool = False,
) -> SandboxSmokeResult:
    return asyncio.run(
        run_backend_smoke_async(
            backend_id,
            case=case,
            skip_unavailable=skip_unavailable,
        )
    )
