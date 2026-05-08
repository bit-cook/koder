"""Propagation helpers for worker permission contexts."""

from __future__ import annotations

from .service import PermissionService


def propagate_permission_context(
    service: PermissionService,
    *,
    worker_name: str,
) -> PermissionService:
    """Create a worker-scoped copy of a permission service."""
    return PermissionService(
        mode=service.mode,
        owner=worker_name,
        workspace_root=service.workspace_root,
        additional_roots=list(service.additional_roots),
        store=service.store,
        denial_log=service.denial_log,
        rules=service.export_rules(),
    )
