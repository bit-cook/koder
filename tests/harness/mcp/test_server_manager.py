from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
import yaml

from koder_agent.config import reset_config_manager
from koder_agent.config.manager import ConfigManager
from koder_agent.mcp.project_approvals import set_project_approval
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
    for name in (
        "node",
        "inside-server",
        "outside-server",
        "parent-server",
        "project-server",
        "root-server",
        "root-shadowed",
        "child-server",
        "child-wins",
        "python",
    ):
        executable = bin_dir / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", tmp_path / ".koder" / "config.yaml")
    reset_config_manager()
    yield tmp_path
    reset_config_manager()


def test_list_servers_merges_user_project_and_local_scopes_with_precedence(isolate_runtime):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_user_config(
        isolate_runtime,
        {
            "mcp_servers": [
                {
                    "name": "shared",
                    "transport_type": "http",
                    "url": "https://user.example/mcp",
                },
                {
                    "name": "user-only",
                    "transport_type": "stdio",
                    "command": "python",
                    "args": ["-m", "user_server"],
                },
            ],
            "mcp_local_projects": [
                {
                    "project_root": str(project),
                    "servers": [
                        {
                            "name": "shared",
                            "transport_type": "stdio",
                            "command": "python",
                            "args": ["-m", "local_server"],
                        },
                        {
                            "name": "local-only",
                            "transport_type": "http",
                            "url": "https://local.example/mcp",
                        },
                    ],
                }
            ],
        },
    )
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "shared": {
                    "type": "http",
                    "url": "${API_BASE_URL:-https://project.example}/mcp",
                },
                "project-only": {
                    "command": "node",
                    "args": ["server.js"],
                    "env": {"API_TOKEN": "${PROJECT_TOKEN:-fallback-token}"},
                },
            }
        },
    )

    manager = MCPServerManager()
    servers = asyncio.run(manager.list_servers(cwd=project))
    by_name = {server.name: server for server in servers}

    assert sorted(by_name) == ["local-only", "project-only", "shared", "user-only"]
    assert by_name["shared"].scope == "local"
    assert by_name["shared"].command == "python"
    assert by_name["project-only"].scope == "project"
    assert by_name["project-only"].env_vars == {
        "API_TOKEN": "fallback-token",
        "PATH": str(isolate_runtime / "reviewed-bin"),
    }
    assert by_name["user-only"].scope == "user"


def test_get_server_reads_project_scope_json_with_variable_expansion(isolate_runtime):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "weather": {
                    "type": "http",
                    "url": "${API_BASE_URL:-https://weather.example}/mcp",
                    "headers": {"Authorization": "Bearer ${API_TOKEN:-test-token}"},
                }
            }
        },
    )

    manager = MCPServerManager()
    server = asyncio.run(manager.get_server("weather", cwd=project))

    assert server is not None
    assert server.scope == "project"
    assert server.url == "https://weather.example/mcp"
    assert server.headers == {"Authorization": "Bearer test-token"}


def test_project_discovery_stops_at_git_repository_root(isolate_runtime):
    outside = isolate_runtime
    repository = outside / "repository"
    nested = repository / "packages" / "app"
    nested.mkdir(parents=True)
    (repository / ".git").mkdir()
    _write_project_mcp(
        outside,
        {"mcpServers": {"outside": {"command": "outside-server"}}},
    )
    _write_project_mcp(
        repository,
        {"mcpServers": {"inside": {"command": "inside-server"}}},
    )

    servers = asyncio.run(MCPServerManager().list_servers(cwd=nested, scope="project"))

    assert [server.name for server in servers] == ["inside"]
    assert servers[0].source_path == str((repository / ".mcp.json").resolve())


def test_project_discovery_without_git_uses_explicit_cwd_boundary(isolate_runtime):
    parent = isolate_runtime / "parent"
    project = parent / "project"
    project.mkdir(parents=True)
    _write_project_mcp(
        parent,
        {"mcpServers": {"parent": {"command": "parent-server"}}},
    )
    _write_project_mcp(
        project,
        {"mcpServers": {"project": {"command": "project-server"}}},
    )

    servers = asyncio.run(MCPServerManager().list_servers(cwd=project, scope="project"))

    assert [server.name for server in servers] == ["project"]


def test_project_merge_preserves_each_contributing_source_identity(isolate_runtime):
    repository = isolate_runtime / "repository"
    nested = repository / "nested"
    nested.mkdir(parents=True)
    (repository / ".git").mkdir()
    _write_project_mcp(
        repository,
        {
            "mcpServers": {
                "root-only": {"command": "root-server"},
                "overridden": {"command": "root-shadowed"},
            }
        },
    )
    _write_project_mcp(
        nested,
        {
            "mcpServers": {
                "child-only": {"command": "child-server"},
                "overridden": {"command": "child-wins"},
            }
        },
    )

    servers = asyncio.run(MCPServerManager().list_servers(cwd=nested, scope="project"))
    by_name = {server.name: server for server in servers}

    root_source = str((repository / ".mcp.json").resolve())
    child_source = str((nested / ".mcp.json").resolve())
    assert by_name["root-only"].source_path == root_source
    assert by_name["child-only"].source_path == child_source
    assert by_name["overridden"].source_path == child_source
    assert by_name["overridden"].command == str(
        (isolate_runtime / "reviewed-bin" / "child-wins").resolve()
    )
    assert by_name["root-only"].source_digest
    assert by_name["child-only"].source_digest
    assert by_name["root-only"].source_digest != by_name["child-only"].source_digest
    assert by_name["child-only"].source_digest == by_name["overridden"].source_digest


def test_project_source_digest_is_deterministic_for_equivalent_config(isolate_runtime):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "z-server": {
                    "command": "python",
                    "args": ["server.py"],
                    "env": {"B": "2", "A": "1"},
                },
                "a-server": {"url": "https://example.com/mcp", "type": "http"},
            }
        },
    )
    manager = MCPServerManager()
    first = asyncio.run(manager.list_servers(cwd=project, scope="project"))

    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "a-server": {"type": "http", "url": "https://example.com/mcp"},
                "z-server": {
                    "env": {"A": "1", "B": "2"},
                    "args": ["server.py"],
                    "command": "python",
                },
            }
        },
    )
    second = asyncio.run(manager.list_servers(cwd=project, scope="project"))

    assert {server.source_digest for server in first} == {server.source_digest for server in second}


def test_project_execution_descriptor_binds_reviewed_path(isolate_runtime, monkeypatch):
    project = isolate_runtime / "project"
    project.mkdir()
    trusted_bin = isolate_runtime / "trusted-bin"
    malicious_bin = isolate_runtime / "repo-bin"
    trusted_bin.mkdir()
    malicious_bin.mkdir()
    for directory, marker in ((trusted_bin, "trusted"), (malicious_bin, "malicious")):
        executable = directory / "safe-mcp"
        executable.write_text(f"#!/bin/sh\n# {marker}\n", encoding="utf-8")
        executable.chmod(0o755)

    monkeypatch.setenv("PATH", str(trusted_bin))
    _write_project_mcp(project, {"mcpServers": {"bound": {"command": "safe-mcp"}}})
    manager = MCPServerManager()
    server = asyncio.run(manager.get_server("bound", cwd=project, scope="project"))
    assert server is not None
    assert server.command == str((trusted_bin / "safe-mcp").resolve())
    assert server.env_vars["PATH"] == str(trusted_bin)
    assert server.execution_descriptor["stdio"]["executable"] == server.command
    set_project_approval(
        project_root=server.project_root,
        source_path=server.source_path,
        source_digest=server.source_digest,
        approved=True,
    )
    assert manager.revalidate_project_config(server) is True

    monkeypatch.setenv("PATH", f"{malicious_bin}{os.pathsep}{trusted_bin}")
    assert manager.revalidate_project_config(server) is False


def test_project_executable_content_swap_invalidates_approval(isolate_runtime):
    project = isolate_runtime / "project"
    bin_dir = project / "bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "mcp"
    executable.write_text("#!/bin/sh\necho safe\n", encoding="utf-8")
    executable.chmod(0o755)
    _write_project_mcp(project, {"mcpServers": {"bound": {"command": "./bin/mcp"}}})
    manager = MCPServerManager()
    server = asyncio.run(manager.get_server("bound", cwd=project, scope="project"))
    assert server is not None
    set_project_approval(
        project_root=server.project_root,
        source_path=server.source_path,
        source_digest=server.source_digest,
        approved=True,
    )
    assert manager.revalidate_project_config(server) is True

    executable.write_text("#!/bin/sh\necho evil\n", encoding="utf-8")
    executable.chmod(0o755)
    assert manager.revalidate_project_config(server) is False


def test_project_symlinked_executable_fails_closed(isolate_runtime, caplog):
    project = isolate_runtime / "project"
    bin_dir = project / "bin"
    bin_dir.mkdir(parents=True)
    target = project / "real-mcp"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o755)
    (bin_dir / "mcp").symlink_to(target)
    _write_project_mcp(project, {"mcpServers": {"linked": {"command": "./bin/mcp"}}})

    with caplog.at_level("WARNING"):
        servers = asyncio.run(MCPServerManager().list_servers(cwd=project, scope="project"))

    assert servers == []
    assert "executable symlinks are not allowed" in caplog.text


def test_invalid_project_entries_do_not_block_user_or_valid_project_server(
    isolate_runtime, monkeypatch
):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_user_config(
        isolate_runtime,
        {
            "mcp_servers": [
                {
                    "name": "trusted-user",
                    "transport_type": "http",
                    "url": "https://user.example/mcp",
                }
            ]
        },
    )
    monkeypatch.delenv("REQUIRED_VAR", raising=False)
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "invalid": {"command": "${REQUIRED_VAR}"},
                "also-invalid": "not-an-object",
                "valid-project": {"type": "http", "url": "https://project.example/mcp"},
            }
        },
    )

    servers = asyncio.run(MCPServerManager().list_servers(cwd=project))

    assert [server.name for server in servers] == ["trusted-user", "valid-project"]


def test_invalid_project_source_does_not_block_user_server(isolate_runtime):
    project = isolate_runtime / "project"
    project.mkdir()
    _write_user_config(
        isolate_runtime,
        {
            "mcp_servers": [
                {
                    "name": "trusted-user",
                    "transport_type": "http",
                    "url": "https://user.example/mcp",
                }
            ]
        },
    )
    (project / ".mcp.json").write_text("{not-json", encoding="utf-8")

    servers = asyncio.run(MCPServerManager().list_servers(cwd=project))

    assert [server.name for server in servers] == ["trusted-user"]


def test_invalid_entry_becoming_valid_invalidates_existing_source_approval(
    isolate_runtime, monkeypatch
):
    project = isolate_runtime / "project"
    project.mkdir()
    monkeypatch.delenv("LATE_EXECUTABLE", raising=False)
    _write_project_mcp(
        project,
        {
            "mcpServers": {
                "valid": {"command": "node"},
                "late": {"command": "${LATE_EXECUTABLE}"},
            }
        },
    )
    manager = MCPServerManager()
    valid = asyncio.run(manager.get_server("valid", cwd=project, scope="project"))
    assert valid is not None
    set_project_approval(
        project_root=valid.project_root,
        source_path=valid.source_path,
        source_digest=valid.source_digest,
        approved=True,
    )
    assert manager.revalidate_project_config(valid) is True

    monkeypatch.setenv("LATE_EXECUTABLE", "node")

    assert manager.revalidate_project_config(valid) is False
