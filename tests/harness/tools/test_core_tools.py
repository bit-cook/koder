import sys
import types
from pathlib import Path

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

    class DDGSException(Exception):  # noqa: N818
        pass

    ddgs_exceptions.DDGSException = DDGSException
    sys.modules["ddgs.exceptions"] = ddgs_exceptions

# Ensure project root is on sys.path when running tests directly
project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.tools.registry import ToolRegistry


def test_core_tool_modules_register_expected_tools():
    registry = ToolRegistry.with_core_tools(
        categories={"code", "file", "search", "web", "mcp"},
    )
    expected = {
        "code_intelligence",
        "read_file",
        "write_file",
        "edit_file",
        "glob_search",
        "grep_search",
        "web_fetch",
        "web_search",
        "list_mcp_resources",
        "read_mcp_resource",
        "tool_search",
    }
    assert expected <= set(registry.list_names())


def test_mcp_module_exposes_invocable_tool_contract():
    registry = ToolRegistry.empty()
    registry.register_module("mcp_ops")
    tool = registry.get("list_mcp_resources")
    assert tool is not None
    assert callable(tool.invoke)
