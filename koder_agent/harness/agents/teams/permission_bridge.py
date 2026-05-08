"""Permission bridge: routes permission requests from workers to leader.

When a worker agent encounters a tool that requires approval, the
bridge forwards the request to the leader's handler (which can show
a UI prompt, auto-approve based on rules, etc.).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class PermissionRequest:
    """A permission request from a worker agent."""

    request_id: str
    worker_name: str
    tool_name: str
    arguments: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class PermissionResponse:
    """Response to a permission request."""

    request_id: str
    approved: bool
    reason: str = ""


PermissionHandler = Callable[[PermissionRequest], Awaitable[PermissionResponse]]


class PermissionBridge:
    """Routes permission requests from workers to leader.

    Usage:
        async def handler(req: PermissionRequest) -> PermissionResponse:
            # Show UI prompt, check rules, etc.
            return PermissionResponse(req.request_id, approved=True)

        bridge = PermissionBridge(handler=handler)
        response = await bridge.request_permission(
            worker_name="worker-1",
            tool_name="run_shell",
            arguments={"command": "echo hi"},
            reason="shell command needs approval",
        )
    """

    def __init__(
        self,
        handler: PermissionHandler,
        timeout: float = 30.0,
    ):
        self._handler = handler
        self._timeout = timeout
        self._counter = 0
        self.history: list[tuple[PermissionRequest, PermissionResponse]] = []

    def _make_request(
        self,
        worker_name: str,
        tool_name: str,
        arguments: dict,
        reason: str,
    ) -> PermissionRequest:
        self._counter += 1
        return PermissionRequest(
            request_id=f"perm-{self._counter}-{uuid.uuid4().hex[:6]}",
            worker_name=worker_name,
            tool_name=tool_name,
            arguments=arguments,
            reason=reason,
        )

    async def request_permission(
        self,
        worker_name: str,
        tool_name: str,
        arguments: dict,
        reason: str,
    ) -> PermissionResponse:
        """Route a permission request to the handler.

        Returns denied response on timeout or handler error.
        """
        req = self._make_request(worker_name, tool_name, arguments, reason)

        try:
            resp = await asyncio.wait_for(
                self._handler(req),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            resp = PermissionResponse(
                request_id=req.request_id,
                approved=False,
                reason="Permission request timeout",
            )
        except Exception as e:
            resp = PermissionResponse(
                request_id=req.request_id,
                approved=False,
                reason=f"Permission handler error: {e}",
            )

        self.history.append((req, resp))
        return resp
