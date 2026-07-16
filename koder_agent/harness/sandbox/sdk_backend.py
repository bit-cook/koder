"""OpenAI Agents SDK sandbox execution adapter."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from koder_agent.harness.session_env import is_probably_secret_env_name

from .backend import SandboxExecutionContext, SandboxExecutionResult
from .enforcement import autoapproval_blockers, sandbox_degradation_reason
from .policy import SandboxPolicy
from .registry import create_backend_client_and_options, get_backend_status, select_backend_id
from .workspace import protected_write_violation, read_only_violation


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Drop secret-looking vars from the env before it enters the sandbox.

    Callers assemble the sandbox env from the host process (which carries API
    keys and tokens). Forwarding those wholesale would leak host credentials
    into sandboxed commands (finding #2), so strip anything that looks like a
    secret here regardless of how the caller built the env.
    """
    return {key: value for key, value in env.items() if not is_probably_secret_env_name(key)}


DELETE_TIMEOUT_SECONDS = 5.0


def _build_manifest(root: Path, env: dict[str, str], *, backend_id: str):
    from agents.sandbox.manifest import Environment, Manifest

    manifest_root = "/workspace" if backend_id == "cloudflare" else str(root)
    return Manifest(root=manifest_root, environment=Environment(value=_scrub_env(env)))


async def _delete_created_session(client, session) -> None:
    """Delete a client-owned sandbox even while the caller is being cancelled."""

    task = asyncio.create_task(client.delete(session))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=DELETE_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=DELETE_TIMEOUT_SECONDS)
        raise
    except asyncio.TimeoutError:
        task.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await task
    except Exception:
        pass


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
            sandboxed=False,
            created=False,
            executed=False,
            violation=violation,
            reason=violation,
        )

    blockers = autoapproval_blockers(policy, status.capabilities)
    if blockers and not context.degradation_approved:
        reason = sandbox_degradation_reason(backend_id, blockers)
        return SandboxExecutionResult(
            status="policy_violation",
            backend_id=backend_id,
            sandboxed=False,
            created=False,
            executed=False,
            violation=reason,
            reason=reason,
        )

    client = None
    session = None
    created = False
    executed = False
    try:
        client, options = create_backend_client_and_options(backend_id, policy=policy)
        manifest = _build_manifest(context.cwd, context.env, backend_id=backend_id)
        session = await client.create(manifest=manifest, options=options)
        created = True
        async with session:
            executed = True
            result = await session.exec(context.command, timeout=context.timeout, shell=True)
        return SandboxExecutionResult(
            status="success" if result.exit_code == 0 else "error",
            stdout=_decode(result.stdout),
            stderr=_decode(result.stderr),
            exit_code=result.exit_code,
            backend_id=backend_id,
            sandboxed=True,
            created=True,
            executed=True,
        )
    except Exception as exc:
        return SandboxExecutionResult(
            status="error",
            backend_id=backend_id,
            sandboxed=created,
            created=created,
            executed=executed,
            reason=f"{type(exc).__name__}: {exc}",
            stderr=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if client is not None and session is not None:
            await _delete_created_session(client, session)
