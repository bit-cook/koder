"""Tests for permission bridge between workers and leader."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from koder_agent.harness.agents.teams.permission_bridge import (
    PermissionBridge,
    PermissionRequest,
    PermissionResponse,
)


def test_permission_request():
    req = PermissionRequest(
        request_id="req1",
        worker_name="worker-1",
        tool_name="run_shell",
        arguments={"command": "rm -rf /tmp/test"},
        reason="dangerous command",
    )
    assert req.request_id == "req1"
    assert req.worker_name == "worker-1"
    assert req.tool_name == "run_shell"


def test_permission_response():
    resp = PermissionResponse(
        request_id="req1",
        approved=True,
        reason="user approved",
    )
    assert resp.approved
    assert resp.request_id == "req1"


@pytest.mark.asyncio
async def test_bridge_routes_request():
    """Bridge should route request to handler and return response."""

    async def handler(req: PermissionRequest) -> PermissionResponse:
        return PermissionResponse(
            request_id=req.request_id,
            approved=True,
            reason="approved by leader",
        )

    bridge = PermissionBridge(handler=handler)
    resp = await bridge.request_permission(
        worker_name="worker-1",
        tool_name="run_shell",
        arguments={"command": "echo hi"},
        reason="shell command",
    )
    assert resp.approved


@pytest.mark.asyncio
async def test_bridge_deny():
    async def handler(req: PermissionRequest) -> PermissionResponse:
        return PermissionResponse(
            request_id=req.request_id,
            approved=False,
            reason="too dangerous",
        )

    bridge = PermissionBridge(handler=handler)
    resp = await bridge.request_permission(
        worker_name="worker-1",
        tool_name="run_shell",
        arguments={"command": "rm -rf /"},
        reason="dangerous",
    )
    assert not resp.approved
    assert "dangerous" in resp.reason


@pytest.mark.asyncio
async def test_bridge_timeout():
    """Should timeout if handler takes too long."""

    async def slow_handler(req):
        await asyncio.sleep(10)
        return PermissionResponse(request_id=req.request_id, approved=True)

    bridge = PermissionBridge(handler=slow_handler, timeout=0.1)
    resp = await bridge.request_permission(
        worker_name="worker-1",
        tool_name="run_shell",
        arguments={},
        reason="test",
    )
    # Should deny on timeout
    assert not resp.approved
    assert "timeout" in resp.reason.lower()


@pytest.mark.asyncio
async def test_bridge_handler_error():
    """Should deny if handler raises."""

    async def failing_handler(req):
        raise RuntimeError("handler crashed")

    bridge = PermissionBridge(handler=failing_handler)
    resp = await bridge.request_permission(
        worker_name="worker-1",
        tool_name="test",
        arguments={},
        reason="test",
    )
    assert not resp.approved
    assert "error" in resp.reason.lower()


def test_bridge_request_counter():
    """Each request should get a unique ID."""
    bridge = PermissionBridge(handler=AsyncMock())
    req1 = bridge._make_request("w1", "tool1", {}, "reason1")
    req2 = bridge._make_request("w1", "tool2", {}, "reason2")
    assert req1.request_id != req2.request_id


@pytest.mark.asyncio
async def test_bridge_tracks_history():
    async def handler(req):
        return PermissionResponse(request_id=req.request_id, approved=True)

    bridge = PermissionBridge(handler=handler)
    await bridge.request_permission("w1", "tool1", {}, "reason")
    await bridge.request_permission("w1", "tool2", {}, "reason")

    assert len(bridge.history) == 2
