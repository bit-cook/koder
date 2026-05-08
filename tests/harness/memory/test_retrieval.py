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

from koder_agent.harness.memory.retrieval import retrieve_relevant_memories


def test_retrieve_relevant_memories_prefers_matching_fixture_notes():
    fixtures_dir = Path("tests/fixtures/memory/retrieval")
    result = retrieve_relevant_memories("dashboard latency", [fixtures_dir], max_tokens=200)

    assert result.memories
    assert result.memories[0].path.name == "project-note.md"


def test_retrieve_relevant_memories_respects_token_budget():
    fixtures_dir = Path("tests/fixtures/memory/retrieval")
    result = retrieve_relevant_memories("user", [fixtures_dir], max_tokens=20)
    assert result.token_count <= 20
