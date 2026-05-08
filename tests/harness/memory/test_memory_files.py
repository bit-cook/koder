import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.memory.memory_files import parse_memory_file


def test_parse_memory_file_reads_frontmatter_and_body():
    parsed = parse_memory_file("---\ntype: note\ndescription: demo\n---\nhello")
    assert parsed.memory_type == "note"
    assert parsed.description == "demo"
    assert parsed.body == "hello"


def test_parse_memory_file_without_frontmatter():
    parsed = parse_memory_file("hello")
    assert parsed.memory_type is None
    assert parsed.body == "hello"
