import asyncio
import sys
import types
from pathlib import Path

import pytest
import yaml

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

if "ddgs" not in sys.modules:
    ddgs_stub = types.ModuleType("ddgs")

    class _StubDDGS:
        def text(self, *_args, **_kwargs):
            return []

    ddgs_stub.DDGS = _StubDDGS
    sys.modules["ddgs"] = ddgs_stub

    ddgs_exceptions = types.ModuleType("ddgs.exceptions")

    class DDGSException(Exception):
        pass

    ddgs_exceptions.DDGSException = DDGSException
    sys.modules["ddgs.exceptions"] = ddgs_exceptions

# Ensure project root is on sys.path when running tests directly
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.config import reset_config_manager
from koder_agent.config.manager import ConfigManager
from koder_agent.harness.tools.registry import ToolRegistry


def _write_config(tmp_path, data: dict) -> None:
    config_dir = tmp_path / ".koder"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture(autouse=True)
def isolate_mcp_config(monkeypatch, tmp_path):
    config_path = Path(tmp_path) / ".koder" / "config.yaml"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.chdir(tmp_path)
    reset_config_manager()
    yield
    reset_config_manager()


def test_mcp_resource_listing_reads_configured_servers(tmp_path):
    _write_config(
        tmp_path,
        {
            "mcp_servers": [
                {
                    "name": "notes",
                    "transport_type": "stdio",
                    "command": "python",
                    "args": ["-m", "notes_server"],
                }
            ]
        },
    )
    registry = ToolRegistry.empty()
    registry.register_module("mcp_ops")

    result = asyncio.run(registry.get("list_mcp_resources").invoke({}))

    assert result["status"] == "success"
    assert result["content"] == [
        {
            "uri": "config://notes",
            "name": "notes",
            "mimeType": "application/x-koder-mcp-config",
            "description": "stdio MCP server configuration",
            "server": "notes",
        }
    ]


def test_mcp_resource_read_reports_unknown_server():
    registry = ToolRegistry.empty()
    registry.register_module("mcp_ops")

    result = asyncio.run(
        registry.get("read_mcp_resource").invoke({"server": "missing", "uri": "config://missing"})
    )

    assert result["status"] == "error"
    assert 'Server "missing" not found' in result["content"]
