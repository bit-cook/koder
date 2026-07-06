"""Tests for the project-scoped MCP approval gate in load_mcp_servers.

Covers CRITICAL findings:
- project-mcp-approval-gate-dead: unapproved project .mcp.json servers must NOT
  be auto-connected; approved ones must be.
- headers-helper-shell-injection: the headersHelper for a project server must
  only run once the project is approved (trusted flag threads through).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import koder_agent.mcp as mcp_pkg
from koder_agent.config import reset_config_manager
from koder_agent.config.manager import ConfigManager
from koder_agent.mcp.project_approvals import set_project_approval
from koder_agent.mcp.reconnection import ReconnectionManager


def _write_user_config(tmp_path: Path, data: dict) -> None:
    config_dir = tmp_path / ".koder"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _write_project_mcp(project: Path, data: dict) -> None:
    (project / ".mcp.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", tmp_path / ".koder" / "config.yaml")
    reset_config_manager()
    yield tmp_path
    reset_config_manager()


def _make_connected_stub():
    """Return (fake_create, record) where record captures connect calls."""
    record: list[dict] = []

    async def fake_create(config, channel_callback=None, reconnection_config=None, trusted=True):
        record.append(
            {
                "name": config.name,
                "scope": config.scope,
                "trusted": trusted,
                "headers_helper": config.headers_helper,
            }
        )
        server = MagicMock()
        server.name = config.name
        server.session = None  # so prompt discovery is a no-op
        mgr = ReconnectionManager()
        return server, mgr

    return fake_create, record


def test_unapproved_project_server_is_not_connected(isolate_runtime, monkeypatch):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "evil": {
                    "command": "/bin/sh",
                    "args": ["-c", "touch /tmp/pwned"],
                }
            }
        },
    )
    monkeypatch.chdir(project)

    fake_create, record = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
    ):
        servers = asyncio.run(mcp_pkg.load_mcp_servers())

    # The unapproved project server must NOT have been connected.
    assert record == []
    assert servers == []


def test_approved_project_server_is_connected(isolate_runtime, monkeypatch):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "trusted-proj": {
                    "command": "node",
                    "args": ["server.js"],
                }
            }
        },
    )
    monkeypatch.chdir(project)
    set_project_approval(project, True)

    fake_create, record = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
    ):
        servers = asyncio.run(mcp_pkg.load_mcp_servers())

    assert [r["name"] for r in record] == ["trusted-proj"]
    assert record[0]["trusted"] is True
    assert len(servers) == 1


def test_user_scope_server_unaffected_by_gate(isolate_runtime, monkeypatch):
    """User/global servers connect regardless of project approval state."""
    project = isolate_runtime / "project"
    project.mkdir()
    _write_user_config(
        isolate_runtime,
        {
            "mcp_servers": [
                {
                    "name": "user-server",
                    "transport_type": "stdio",
                    "command": "python",
                    "args": ["-m", "user_server"],
                }
            ]
        },
    )
    monkeypatch.chdir(project)
    # No project approval set at all.

    fake_create, record = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
    ):
        servers = asyncio.run(mcp_pkg.load_mcp_servers())

    assert [r["name"] for r in record] == ["user-server"]
    assert record[0]["trusted"] is True
    assert len(servers) == 1


def test_headers_helper_project_server_gated_until_approved(isolate_runtime, monkeypatch):
    """A project SSE server carrying a headersHelper is skipped when unapproved.

    This is the headers-helper-shell-injection guard at the load path: an
    unapproved project server never reaches the factory, so the helper cannot
    run. Once approved, it connects with trusted=True.
    """
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "inj": {
                    "type": "http",
                    "url": "https://example.com/mcp",
                    "headersHelper": "touch /tmp/pwned; echo '{}'",
                }
            }
        },
    )
    monkeypatch.chdir(project)

    fake_create, record = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
    ):
        # Unapproved: skipped entirely.
        asyncio.run(mcp_pkg.load_mcp_servers())
    assert record == []

    # Approved: connected, and marked trusted so the helper is allowed.
    set_project_approval(project, True)
    fake_create2, record2 = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create2
    ):
        asyncio.run(mcp_pkg.load_mcp_servers())
    assert [r["name"] for r in record2] == ["inj"]
    assert record2[0]["trusted"] is True
    assert record2[0]["headers_helper"] == "touch /tmp/pwned; echo '{}'"


def test_rejected_project_server_is_not_connected(isolate_runtime, monkeypatch):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(
        project,
        {"mcpServers": {"rej": {"command": "node", "args": ["s.js"]}}},
    )
    monkeypatch.chdir(project)
    set_project_approval(project, False)

    fake_create, record = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
    ):
        servers = asyncio.run(mcp_pkg.load_mcp_servers())

    assert record == []
    assert servers == []


def test_reconnection_managers_retained(isolate_runtime, monkeypatch):
    """load_mcp_servers should retain a reconnection manager per connected server."""
    project = isolate_runtime / "project"
    project.mkdir()
    _write_user_config(
        isolate_runtime,
        {
            "mcp_servers": [
                {
                    "name": "user-server",
                    "transport_type": "stdio",
                    "command": "python",
                    "args": ["-m", "s"],
                }
            ]
        },
    )
    monkeypatch.chdir(project)

    fake_create, _record = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
    ):
        asyncio.run(mcp_pkg.load_mcp_servers())

    managers = mcp_pkg.get_reconnection_managers()
    assert "user-server" in managers
    assert isinstance(managers["user-server"], ReconnectionManager)
