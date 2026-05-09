"""Shared sandbox backend data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SandboxBackendCapabilities:
    """Capability summary for one sandbox backend."""

    supports_shell: bool = True
    supports_filesystem: bool = True
    supports_pty: str = "unknown"
    supports_background: bool = False
    supports_network_policy: str = "unknown"
    supports_protected_paths: str = "unknown"


@dataclass(frozen=True)
class SandboxBackendStatus:
    """Availability record for one configured sandbox backend."""

    backend_id: str
    selected: bool = False
    available: bool = False
    reason: str = "not checked"
    dependency_errors: tuple[str, ...] = ()
    credential_errors: tuple[str, ...] = ()
    capabilities: SandboxBackendCapabilities = field(default_factory=SandboxBackendCapabilities)
    validation_tier: str = "mocked-unit"
    setup_hint: str | None = None
    last_smoke: str | None = None

    @property
    def unavailable_reasons(self) -> tuple[str, ...]:
        reasons = [*self.dependency_errors, *self.credential_errors]
        if not reasons and not self.available and self.reason:
            reasons.append(self.reason)
        return tuple(reasons)


@dataclass(frozen=True)
class SandboxExecutionContext:
    """Runtime inputs for a sandboxed shell execution request."""

    cwd: Path
    repo_root: Path
    command: str
    env: dict[str, str]
    timeout: int
    background: bool
    session_id: str | None
    policy: object
    output_limit: int | None = None


@dataclass(frozen=True)
class SandboxExecutionResult:
    """Result returned by a sandbox backend adapter."""

    status: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    backend_id: str | None = None
    sandboxed: bool = False
    violation: str | None = None
    reason: str | None = None

    def combined_output(self) -> str:
        output = self.stdout.strip()
        stderr = self.stderr.strip()
        if stderr:
            output = f"{output}\n[stderr]: {stderr}" if output else f"[stderr]: {stderr}"
        if self.exit_code not in (None, 0):
            output = (
                f"{output}\n[exit code]: {self.exit_code}"
                if output
                else f"[exit code]: {self.exit_code}"
            )
        if self.violation:
            output = f"sandbox violation: {self.violation}" + (f"\n{output}" if output else "")
        if not output:
            output = "(no output)"
        return output
