import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

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


@pytest.fixture
def isolated_mcp_home(monkeypatch, tmp_path):
    config_path = Path(tmp_path) / ".koder" / "config.yaml"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", config_path)
    reset_config_manager()
    yield tmp_path
    reset_config_manager()


def test_file_tool_transcript_shapes(tmp_path):
    registry = ToolRegistry.empty()
    registry.register_module("file_ops")

    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\nbeta\n", encoding="utf-8")

    success = asyncio.run(registry.get("read_file").invoke({"file_path": str(file_path)}))
    error = asyncio.run(
        registry.get("read_file").invoke({"file_path": str(tmp_path / "missing.txt")})
    )

    assert success["tool"] == "read_file"
    assert success["status"] == "success"
    assert set(success) == {"tool", "status", "content"}
    assert "1|alpha" in success["content"]

    assert error["tool"] == "read_file"
    assert error["status"] == "error"
    assert set(error) == {"tool", "status", "content"}
    assert "File not found" in error["content"]


def test_file_tool_transcript_rejects_conflicting_path_aliases(tmp_path):
    registry = ToolRegistry.empty()
    registry.register_module("file_ops")
    safe = tmp_path / "safe.txt"
    outside = tmp_path.parent / "outside.txt"

    result = asyncio.run(
        registry.get("write_file").invoke(
            {
                "path": str(safe),
                "file_path": str(outside),
                "content": "must not be written",
            }
        )
    )

    assert result["status"] == "error"
    assert "must be exactly equal" in result["content"]
    assert not safe.exists()
    assert not outside.exists()


def test_web_tool_transcript_shapes():
    registry = ToolRegistry.empty()
    registry.register_module("web_ops")

    with patch("koder_agent.tools.web.requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"<html><body>Hello world</body></html>"
        mock_response.text = "<html><body>Hello world</body></html>"
        mock_response.headers = {"content-type": "text/html"}
        mock_get.return_value = mock_response

        success = asyncio.run(
            registry.get("web_fetch").invoke({"url": "https://example.com", "prompt": "summarize"})
        )

    error = asyncio.run(registry.get("web_search").invoke({"query": ""}))

    assert success["tool"] == "web_fetch"
    assert success["status"] == "success"
    assert set(success) == {"tool", "status", "content"}
    assert "https://example.com" in success["content"]

    assert error["tool"] == "web_search"
    assert error["status"] == "error"
    assert set(error) == {"tool", "status", "content"}
    assert "Invalid query" in error["content"]


def test_mcp_tool_transcript_shapes(isolated_mcp_home):
    _write_config(
        isolated_mcp_home,
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

    success = asyncio.run(registry.get("list_mcp_resources").invoke({}))
    error = asyncio.run(
        registry.get("read_mcp_resource").invoke({"server": "missing", "uri": "config://missing"})
    )

    assert success["tool"] == "list_mcp_resources"
    assert success["status"] == "success"
    assert set(success) == {"tool", "status", "content"}
    assert success["content"][0]["server"] == "notes"

    assert error["tool"] == "read_mcp_resource"
    assert error["status"] == "error"
    assert set(error) == {"tool", "status", "content"}
    assert 'Server "missing" not found' in error["content"]


def test_code_intelligence_tool_transcript_shapes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "sample.py"
    source.write_text("def helper(value):\n    return value\n", encoding="utf-8")

    registry = ToolRegistry.empty()
    registry.register_module("code_intelligence_ops")

    success = asyncio.run(
        registry.get("code_intelligence").invoke(
            {"operation": "document_symbols", "path": str(source)}
        )
    )
    error = asyncio.run(registry.get("code_intelligence").invoke({"operation": "hover"}))

    assert success["tool"] == "code_intelligence"
    assert success["status"] == "success"
    assert set(success) == {"tool", "status", "content"}
    assert "sample.py:1:1 function helper" in success["content"]

    assert error["tool"] == "code_intelligence"
    assert error["status"] == "error"
    assert set(error) == {"tool", "status", "content"}
    assert "Unsupported operation" in error["content"]
