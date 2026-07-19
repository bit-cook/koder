"""Truthful policy-enforcement accounting for sandbox auto-approval."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .backend import SandboxBackendCapabilities
from .policy import SandboxPolicy

EnforcementSource = Literal["backend", "preflight", "unenforced"]


@dataclass(frozen=True)
class PolicyEnforcement:
    """How one active policy restriction is enforced for an invocation."""

    restriction: str
    source: EnforcementSource
    detail: str

    @property
    def enforced(self) -> bool:
        return self.source != "unenforced"


def active_policy_enforcements(
    policy: SandboxPolicy,
    capabilities: SandboxBackendCapabilities,
) -> tuple[PolicyEnforcement, ...]:
    """Return enforcement truth for every active sandbox policy restriction.

    Auto-approval is safe only when every returned item is enforced by the
    backend. Command parsing is defense in depth, not an enforcement boundary:
    scripts, interpreters, pipelines, substitutions, subshells, and
    option-directed outputs can hide their actual read/write targets.
    """

    items: list[PolicyEnforcement] = []

    if policy.mode in {"workspace-write", "read-only"}:
        workspace_enforced = capabilities.supports_workspace_isolation == "enforced"
        items.append(
            PolicyEnforcement(
                restriction="workspace isolation",
                source="backend" if workspace_enforced else "unenforced",
                detail=(
                    "backend confines execution to the sandbox workspace"
                    if workspace_enforced
                    else "backend workspace confinement is not proven"
                ),
            )
        )

    if policy.mode == "read-only":
        read_only_enforced = capabilities.supports_read_only_filesystem == "enforced"
        items.append(
            PolicyEnforcement(
                restriction="read-only mode",
                source="backend" if read_only_enforced else "unenforced",
                detail=(
                    "backend mounts the entire workspace read-only"
                    if read_only_enforced
                    else "command preflight is not complete read-only enforcement"
                ),
            )
        )

    if policy.allowed_domains or policy.denied_domains:
        domains_enforced = capabilities.supports_domain_policy == "enforced"
        items.append(
            PolicyEnforcement(
                restriction="domain network policy",
                source="backend" if domains_enforced else "unenforced",
                detail=(
                    "backend enforces domain allow/deny policy"
                    if domains_enforced
                    else "allowedDomains/deniedDomains are not translated to the backend"
                ),
            )
        )
    elif not policy.network_access:
        network_enforced = capabilities.supports_network_policy == "enforced"
        items.append(
            PolicyEnforcement(
                restriction="network access disabled",
                source="backend" if network_enforced else "unenforced",
                detail=(
                    "backend disables internet access"
                    if network_enforced
                    else "networkAccess=false is not enforced by the backend"
                ),
            )
        )

    unsupported_path_fields = (
        ("writableRoots", policy.writable_roots),
        ("allowRead", policy.allow_read),
        ("denyRead", policy.deny_read),
        ("allowWrite", policy.allow_write),
    )
    for field_name, values in unsupported_path_fields:
        if values:
            items.append(
                PolicyEnforcement(
                    restriction=field_name,
                    source="unenforced",
                    detail=f"{field_name} is not translated to preflight or backend policy",
                )
            )

    if policy.protected_paths:
        protected_enforced = capabilities.supports_protected_paths == "enforced"
        items.append(
            PolicyEnforcement(
                restriction="protectedPaths",
                source="backend" if protected_enforced else "unenforced",
                detail=(
                    "backend makes protected subpaths read-only"
                    if protected_enforced
                    else "command preflight cannot enforce protected subpaths"
                ),
            )
        )
    if policy.deny_write:
        protected_enforced = capabilities.supports_protected_paths == "enforced"
        items.append(
            PolicyEnforcement(
                restriction="denyWrite",
                source="backend" if protected_enforced else "unenforced",
                detail=(
                    "backend makes denied write targets read-only"
                    if protected_enforced
                    else "command preflight cannot enforce denied write targets"
                ),
            )
        )

    return tuple(items)


def unenforced_policy_restrictions(
    policy: SandboxPolicy,
    capabilities: SandboxBackendCapabilities,
) -> tuple[PolicyEnforcement, ...]:
    """Return active restrictions that are not actually enforced."""

    return tuple(
        item for item in active_policy_enforcements(policy, capabilities) if not item.enforced
    )


def autoapproval_blockers(
    policy: SandboxPolicy,
    capabilities: SandboxBackendCapabilities,
) -> tuple[PolicyEnforcement, ...]:
    """Return every missing guarantee that prevents sandbox auto-approval."""

    mandatory = (
        (
            "host process isolation",
            capabilities.supports_host_process_isolation,
            "backend isolates the command from host processes, signals, and IPC",
        ),
        (
            "workspace materialization and isolation",
            capabilities.supports_workspace_isolation,
            "backend materializes the canonical workspace and confines writes to it",
        ),
        (
            "repository synchronization",
            capabilities.supports_repository_sync,
            "backend materializes the repository and returns changes to the host",
        ),
    )
    blockers = [
        PolicyEnforcement(
            restriction=name,
            source="unenforced",
            detail=f"{detail} is not proven",
        )
        for name, value, detail in mandatory
        if value != "enforced"
    ]
    blockers.extend(
        item
        for item in unenforced_policy_restrictions(policy, capabilities)
        if item.restriction != "workspace isolation"
    )
    return tuple(blockers)


def sandbox_degradation_reason(
    backend_id: str,
    blockers: tuple[PolicyEnforcement, ...],
) -> str:
    """Name every unavailable guarantee requiring explicit degradation approval."""

    details = "; ".join(f"{item.restriction}: {item.detail}" for item in blockers)
    return (
        f"backend={backend_id}; unavailable configured sandbox guarantees: {details}. "
        "This is a separate sandbox-degradation approval; a generic command or mutation "
        "approval does not accept these losses"
    )


def _configured_values(name: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(repr(value) for value in values)
    return f"{name}=[{rendered}]"


def unsandboxed_fallback_losses(
    policy: SandboxPolicy,
    capabilities: SandboxBackendCapabilities,
    *,
    canonical_cwd: str,
) -> tuple[str, ...]:
    """Enumerate every resolved guarantee lost by executing on the host."""

    losses = [
        "host process isolation "
        f"(backend capability={capabilities.supports_host_process_isolation})",
        "workspace materialization and isolation "
        f"(cwd={canonical_cwd}; backend capability={capabilities.supports_workspace_isolation})",
        f"repository synchronization (backend capability={capabilities.supports_repository_sync})",
    ]

    if policy.mode == "read-only":
        losses.append(
            "read-only mode (mode=read-only; "
            f"backend capability={capabilities.supports_read_only_filesystem})"
        )
    elif policy.mode == "workspace-write":
        losses.append("workspace-write mode (mode=workspace-write)")
    else:
        losses.append(f"sandbox mode={policy.mode}")

    if not policy.network_access:
        losses.append(
            "network access restriction (networkAccess=false; "
            f"backend capability={capabilities.supports_network_policy})"
        )
    if policy.allowed_domains:
        losses.append(
            _configured_values("allowedDomains", policy.allowed_domains)
            + f" (backend capability={capabilities.supports_domain_policy})"
        )
    if policy.denied_domains:
        losses.append(
            _configured_values("deniedDomains", policy.denied_domains)
            + f" (backend capability={capabilities.supports_domain_policy})"
        )

    configured_paths = (
        ("writableRoots", policy.writable_roots),
        ("allowRead", policy.allow_read),
        ("denyRead", policy.deny_read),
        ("allowWrite", policy.allow_write),
        ("denyWrite", policy.deny_write),
        ("protectedPaths", policy.protected_paths),
    )
    for name, values in configured_paths:
        if not values:
            continue
        capability = (
            f"; backend capability={capabilities.supports_protected_paths}"
            if name in {"denyWrite", "protectedPaths"}
            else ""
        )
        losses.append(f"{_configured_values(name, values)}{capability}")

    return tuple(losses)


def unsandboxed_fallback_reason(
    backend_id: str,
    *,
    trigger: str,
    losses: tuple[str, ...],
) -> str:
    """Render the exact approval reason for one unsandboxed fallback."""

    return (
        f"backend={backend_id}; {trigger}; UNSANDBOXED execution loses these exact "
        f"configured sandbox guarantees: {'; '.join(losses)}. This is a separate "
        "sandbox-degradation approval; a generic command or mutation approval does not "
        "accept these losses"
    )


def sandbox_fallback_requirement_digest(
    *,
    command: str,
    trigger: str,
    losses: tuple[str, ...],
    sandbox_enabled: bool,
    platform_enabled: bool,
    backend_available: bool,
    backend_reason: str,
    backend_status_available: bool,
    backend_status_reason: str,
) -> str:
    """Digest fallback-specific state not covered by cwd/policy/capabilities."""

    return _stable_digest(
        {
            "command": command,
            "trigger": trigger,
            "losses": losses,
            "sandbox_enabled": sandbox_enabled,
            "platform_enabled": platform_enabled,
            "backend_available": backend_available,
            "backend_reason": backend_reason,
            "backend_status_available": backend_status_available,
            "backend_status_reason": backend_status_reason,
        }
    )


def canonical_workspace_path(path: Path) -> str:
    """Return the canonical cwd identity used by auto-approval snapshots."""

    return str(path.expanduser().resolve(strict=False))


def _stable_digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sandbox_policy_digest(policy: SandboxPolicy) -> str:
    """Digest every resolved policy field, including the raw source mapping."""

    return _stable_digest(asdict(policy))


def backend_capability_digest(capabilities: SandboxBackendCapabilities) -> str:
    """Digest the complete backend capability snapshot."""

    return _stable_digest(asdict(capabilities))
