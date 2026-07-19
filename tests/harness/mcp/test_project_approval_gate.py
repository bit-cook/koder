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
from koder_agent.mcp.project_approvals import reset_project_choices, set_project_approval
from koder_agent.mcp.reconnection import ReconnectionManager
from koder_agent.mcp.runtime_authorization import (
    attach_project_authorization_validator,
    validate_project_server_authorizations,
)
from koder_agent.mcp.server_manager import MCPServerManager


def _write_user_config(tmp_path: Path, data: dict) -> None:
    config_dir = tmp_path / ".koder"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _write_project_mcp(project: Path, data: dict) -> None:
    (project / ".mcp.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    bin_dir = tmp_path / "reviewed-bin"
    bin_dir.mkdir()
    for name in ("node", "root-command", "child-command", "python", "python3", "helper"):
        executable = bin_dir / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
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


def _approve_project_server(project: Path, name: str, approved: bool = True) -> None:
    server = asyncio.run(MCPServerManager().get_server(name, cwd=project, scope="project"))
    assert server is not None
    assert server.source_path
    assert server.source_digest
    assert server.project_root
    set_project_approval(
        project_root=server.project_root,
        source_path=server.source_path,
        source_digest=server.source_digest,
        approved=approved,
    )


def test_unapproved_project_server_is_not_connected(isolate_runtime, monkeypatch, caplog):
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
    with (
        caplog.at_level("WARNING"),
        patch.object(
            mcp_pkg.MCPServerFactory,
            "create_and_connect_with_retry",
            side_effect=fake_create,
        ),
    ):
        servers = asyncio.run(mcp_pkg.load_mcp_servers())

    # The unapproved project server must NOT have been connected.
    assert record == []
    assert servers == []
    warning = next(
        record.message for record in caplog.records if "Approval required" in record.message
    )
    assert str((project / ".mcp.json").resolve()) in warning
    assert "touch /tmp/pwned" not in warning


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
    _approve_project_server(project, "trusted-proj")

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


def test_project_headers_helper_rejects_shell_expansion(isolate_runtime, monkeypatch, caplog):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "inj": {
                    "type": "http",
                    "url": "https://example.com/mcp",
                    "headersHelper": 'eval "$PAYLOAD"',
                }
            }
        },
    )
    monkeypatch.chdir(project)

    monkeypatch.setenv("PAYLOAD", "benign")
    fake_create, record = _make_connected_stub()
    with (
        caplog.at_level("WARNING"),
        patch.object(
            mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
        ),
    ):
        asyncio.run(mcp_pkg.load_mcp_servers())
    assert record == []
    assert "shell metacharacters and substitutions are not allowed" in caplog.text


def test_safe_project_headers_helper_is_reviewed_and_connected(isolate_runtime, monkeypatch):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "safe-helper": {
                    "type": "http",
                    "url": "https://example.com/mcp",
                    "headersHelper": "helper --json",
                }
            }
        },
    )
    monkeypatch.chdir(project)
    server = asyncio.run(MCPServerManager().get_server("safe-helper", cwd=project, scope="project"))
    assert server is not None
    assert server.headers_helper_argv
    assert server.headers_helper_argv[0] == str((isolate_runtime / "reviewed-bin/helper").resolve())
    _approve_project_server(project, "safe-helper")
    fake_create, record = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
    ):
        asyncio.run(mcp_pkg.load_mcp_servers())
    assert [item["name"] for item in record] == ["safe-helper"]
    assert record[0]["trusted"] is True
    assert record[0]["headers_helper"] == "helper --json"


def test_rejected_project_server_is_not_connected(isolate_runtime, monkeypatch):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(
        project,
        {"mcpServers": {"rej": {"command": "node", "args": ["s.js"]}}},
    )
    monkeypatch.chdir(project)
    _approve_project_server(project, "rej", approved=False)

    fake_create, record = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
    ):
        servers = asyncio.run(mcp_pkg.load_mcp_servers())

    assert record == []
    assert servers == []


def test_approving_one_project_source_does_not_approve_another(isolate_runtime, monkeypatch):
    repository = isolate_runtime / "repository"
    child = repository / "child"
    child.mkdir(parents=True)
    (repository / ".git").mkdir()
    _write_project_mcp(
        repository,
        {"mcpServers": {"root-server": {"command": "root-command"}}},
    )
    _write_project_mcp(
        child,
        {"mcpServers": {"child-server": {"command": "child-command"}}},
    )
    monkeypatch.chdir(child)
    _approve_project_server(child, "root-server")

    fake_create, record = _make_connected_stub()
    with patch.object(
        mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", side_effect=fake_create
    ):
        asyncio.run(mcp_pkg.load_mcp_servers())

    assert [entry["name"] for entry in record] == ["root-server"]


@pytest.mark.parametrize(
    ("initial", "changed"),
    [
        (
            {"command": "python", "args": ["server.py"]},
            {"command": "python3", "args": ["server.py"]},
        ),
        (
            {"type": "http", "url": "https://one.example/mcp"},
            {"type": "http", "url": "https://two.example/mcp"},
        ),
        (
            {"command": "python", "env": {"MODE": "safe"}},
            {"command": "python", "env": {"MODE": "unsafe"}},
        ),
        (
            {"command": "python", "cacheToolsList": False},
            {"command": "python", "cacheToolsList": True},
        ),
    ],
    ids=["command", "url", "env", "config"],
)
def test_project_source_change_invalidates_approval(isolate_runtime, monkeypatch, initial, changed):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(project, {"mcpServers": {"mutable": initial}})
    monkeypatch.chdir(project)
    _approve_project_server(project, "mutable")

    _write_project_mcp(project, {"mcpServers": {"mutable": changed}})
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
        servers = asyncio.run(mcp_pkg.load_mcp_servers())

    managers = mcp_pkg.get_reconnection_managers(servers)
    assert "user-server" in managers
    assert isinstance(managers["user-server"], ReconnectionManager)


def test_load_mcp_servers_cleans_connected_servers_on_fatal_failure(
    isolate_runtime,
    monkeypatch,
):
    class FatalLoadFailure(BaseException):
        pass

    class ConnectedServer:
        name = "first"
        session = None

        def __init__(self):
            self.cleanup_calls = 0

        async def cleanup(self):
            self.cleanup_calls += 1

    project = isolate_runtime / "project"
    project.mkdir()
    _write_user_config(
        isolate_runtime,
        {
            "mcp_servers": [
                {
                    "name": "first",
                    "transport_type": "stdio",
                    "command": "python",
                },
                {
                    "name": "second",
                    "transport_type": "stdio",
                    "command": "python",
                },
            ]
        },
    )
    monkeypatch.chdir(project)
    first = ConnectedServer()

    async def create(config, **_kwargs):
        if config.name == "first":
            return first, ReconnectionManager()
        raise FatalLoadFailure("fatal second server")

    with patch.object(mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", create):
        with pytest.raises(FatalLoadFailure, match="fatal second server"):
            asyncio.run(mcp_pkg.load_mcp_servers())

    assert first.cleanup_calls == 1
    assert mcp_pkg.get_reconnection_managers() == {}


def test_load_mcp_servers_cleans_connected_servers_on_cancellation(
    isolate_runtime,
    monkeypatch,
):
    class ConnectedServer:
        name = "first"
        session = None

        def __init__(self):
            self.cleanup_calls = 0

        async def cleanup(self):
            self.cleanup_calls += 1

    project = isolate_runtime / "project"
    project.mkdir()
    _write_user_config(
        isolate_runtime,
        {
            "mcp_servers": [
                {
                    "name": "first",
                    "transport_type": "stdio",
                    "command": "python",
                },
                {
                    "name": "second",
                    "transport_type": "stdio",
                    "command": "python",
                },
            ]
        },
    )
    monkeypatch.chdir(project)
    first = ConnectedServer()

    async def scenario():
        second_started = asyncio.Event()

        async def create(config, **_kwargs):
            if config.name == "first":
                return first, ReconnectionManager()
            second_started.set()
            await asyncio.Event().wait()

        with patch.object(mcp_pkg.MCPServerFactory, "create_and_connect_with_retry", create):
            task = asyncio.create_task(mcp_pkg.load_mcp_servers())
            await second_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())

    assert first.cleanup_calls == 1
    assert mcp_pkg.get_reconnection_managers() == {}


def test_healthy_project_server_is_disabled_but_retained_after_reset(isolate_runtime, monkeypatch):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(project, {"mcpServers": {"healthy": {"command": "node"}}})
    monkeypatch.chdir(project)
    config = asyncio.run(MCPServerManager().get_server("healthy", cwd=project, scope="project"))
    assert config is not None
    _approve_project_server(project, "healthy")

    class HealthyServer:
        session = object()

        def __init__(self):
            self.cleaned = False

        async def cleanup(self):
            self.cleaned = True
            self.session = None

    healthy = HealthyServer()
    reconnect = ReconnectionManager()

    async def connect_fn():
        raise AssertionError("revoked project server must not reconnect")

    reconnect.bind(config=config, server=healthy, connect_fn=connect_fn)
    assert reset_project_choices(project) == 1

    assert asyncio.run(reconnect.reconnect_if_needed()) is False
    assert healthy.cleaned is True
    assert reconnect.server is healthy
    asyncio.run(reconnect.cleanup())
    assert reconnect.server is None


def test_inline_project_server_validator_disables_handle_at_turn_boundary(
    isolate_runtime, monkeypatch
):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(project, {"mcpServers": {"inline": {"command": "node"}}})
    monkeypatch.chdir(project)
    config = asyncio.run(MCPServerManager().get_server("inline", cwd=project, scope="project"))
    assert config is not None
    _approve_project_server(project, "inline")

    class InlineServer:
        name = "inline"

        def __init__(self):
            self.cleaned = False

        async def cleanup(self):
            self.cleaned = True

    server = InlineServer()
    validator = attach_project_authorization_validator(server, config)
    assert validator is not None
    assert reset_project_choices(project) == 1

    results = asyncio.run(validate_project_server_authorizations([server]))

    assert results == {"inline": False}
    assert server.cleaned is True
    with pytest.raises(PermissionError, match="reviewed execution identity changed"):
        asyncio.run(validator.require_authorized())
