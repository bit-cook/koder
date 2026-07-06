"""OpenAI Agents SDK sandbox execution adapter."""

from __future__ import annotations

from pathlib import Path

from koder_agent.harness.session_env import is_probably_secret_env_name

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


def _scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Drop secret-looking vars from the env before it enters the sandbox.

    Callers assemble the sandbox env from the host process (which carries API
    keys and tokens). Forwarding those wholesale would leak host credentials
    into sandboxed commands (finding #2), so strip anything that looks like a
    secret here regardless of how the caller built the env.
    """
    return {key: value for key, value in env.items() if not is_probably_secret_env_name(key)}


def _build_manifest(root: Path, env: dict[str, str]):
    from agents.sandbox.manifest import Environment, Manifest

    return Manifest(root=str(root), environment=Environment(value=_scrub_env(env)))


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

    # Honesty-first (finding #4): the resolved backend may be unable to enforce
    # the network policy the user configured. The SDK Manifest exposes no network
    # controls, so we cannot silently claim enforcement — surface it explicitly.
    network_note = None
    if policy.network_restricted_but_unenforced:
        network_note = (
            f"[sandbox] network policy is NOT enforced by backend {backend_id}; "
            "network_access/allowed_domains/denied_domains are advisory only"
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
        stderr = _decode(result.stderr)
        if network_note:
            stderr = f"{network_note}\n{stderr}" if stderr else network_note
        return SandboxExecutionResult(
            status="success" if result.exit_code == 0 else "error",
            stdout=_decode(result.stdout),
            stderr=stderr,
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
