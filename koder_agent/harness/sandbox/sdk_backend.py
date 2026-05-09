"""OpenAI Agents SDK sandbox execution adapter."""

from __future__ import annotations

from pathlib import Path

from .backend import SandboxExecutionContext, SandboxExecutionResult
from .policy import SandboxPolicy
from .registry import create_backend_client_and_options, get_backend_status, select_backend_id
from .workspace import protected_write_violation, read_only_violation


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _build_manifest(root: Path, env: dict[str, str]):
    from agents.sandbox.manifest import Environment, Manifest

    return Manifest(root=str(root), environment=Environment(value=env))


async def execute_with_sdk_backend(
    context: SandboxExecutionContext,
) -> SandboxExecutionResult:
    """Run a foreground shell command through the selected SDK sandbox backend."""

    policy = context.policy
    if not isinstance(policy, SandboxPolicy):
        return SandboxExecutionResult(
            status="error",
            reason="invalid sandbox policy",
            sandboxed=False,
        )

    backend_id = select_backend_id(policy.backend)
    status = get_backend_status(backend_id, selected=True)
    if not status.available:
        return SandboxExecutionResult(
            status="unavailable",
            backend_id=backend_id,
            sandboxed=False,
            reason="; ".join(status.unavailable_reasons) or status.reason,
        )

    if context.background:
        return SandboxExecutionResult(
            status="unsupported",
            backend_id=backend_id,
            sandboxed=False,
            reason="background sandbox execution is not implemented for this backend",
        )

    violation = read_only_violation(context.command, policy=policy)
    if violation is None:
        violation = protected_write_violation(
            context.command,
            policy=policy,
            repo_root=context.repo_root,
        )
    if violation is not None:
        return SandboxExecutionResult(
            status="policy_violation",
            backend_id=backend_id,
            sandboxed=True,
            violation=violation,
            reason=violation,
        )

    client = None
    session = None
    try:
        client, options = create_backend_client_and_options(backend_id)
        manifest = _build_manifest(context.cwd, context.env)
        session = await client.create(manifest=manifest, options=options)
        async with session:
            result = await session.exec(context.command, timeout=context.timeout, shell=True)
        await client.delete(session)
        return SandboxExecutionResult(
            status="success" if result.exit_code == 0 else "error",
            stdout=_decode(result.stdout),
            stderr=_decode(result.stderr),
            exit_code=result.exit_code,
            backend_id=backend_id,
            sandboxed=True,
        )
    except Exception as exc:
        if client is not None and session is not None:
            try:
                await client.delete(session)
            except Exception:
                pass
        return SandboxExecutionResult(
            status="error",
            backend_id=backend_id,
            sandboxed=True,
            reason=f"{type(exc).__name__}: {exc}",
            stderr=f"{type(exc).__name__}: {exc}",
        )
