"""Workspace and filesystem policy helpers for sandboxed shell execution."""

from __future__ import annotations

import shlex
from pathlib import Path

from koder_agent.harness.permissions.shell_classifier import classify_shell_command

from .policy import SandboxPolicy

WRITE_COMMANDS = {
    "cat",
    "chmod",
    "chown",
    "cp",
    "dd",
    "install",
    "ln",
    "mkdir",
    "mv",
    "rm",
    "rmdir",
    "tee",
    "touch",
    "truncate",
}


def _token_paths(command: str) -> tuple[str, ...]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ()
    return tuple(token for token in tokens if token and not token.startswith("-"))


def _looks_like_write_command(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True
    if not tokens:
        return False
    if tokens[0] in WRITE_COMMANDS:
        return True
    return classify_shell_command(command).requires_approval


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def protected_write_violation(
    command: str,
    *,
    policy: SandboxPolicy,
    repo_root: Path,
) -> str | None:
    """Best-effort exact-path preflight for protected metadata writes.

    The SDK local backend enforces workspace escapes at the OS boundary. Some backends do not
    expose read-only carve-outs for subpaths inside the writable workspace, so Koder adds a
    conservative exact-token preflight before running commands in the sandbox.
    """

    if not _looks_like_write_command(command):
        return None

    protected_roots = policy.protected_path_roots(repo_root)
    for token in _token_paths(command):
        if token in WRITE_COMMANDS or token in {"sh", "bash", "zsh", "python", "python3"}:
            continue
        if ":" in token and not token.startswith("/"):
            continue
        raw = Path(token).expanduser()
        if not raw.is_absolute():
            raw = repo_root / raw
        candidate = raw.resolve(strict=False)
        for protected_root in protected_roots:
            if candidate == protected_root or _is_under(candidate, protected_root):
                try:
                    display = candidate.relative_to(repo_root)
                except ValueError:
                    display = candidate
                return f"write targets protected path {display}"
    return None


def read_only_violation(command: str, *, policy: SandboxPolicy) -> str | None:
    if policy.mode != "read-only":
        return None
    decision = classify_shell_command(command)
    if decision.requires_approval or not decision.allowed:
        return "read-only sandbox mode does not allow mutating shell commands"
    return None
