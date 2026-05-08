from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from koder_agent.config import reset_config_manager
from koder_agent.config.manager import ConfigManager
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
    assert by_name["project-only"].env_vars == {"API_TOKEN": "fallback-token"}
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
