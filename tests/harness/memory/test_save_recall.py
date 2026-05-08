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

from koder_agent.harness.memory.memory_files import save_memory_file
from koder_agent.harness.memory.retrieval import retrieve_relevant_memories


def test_save_and_recall_memory_note(tmp_path):
    memory_dir = tmp_path / "memories"
    path = save_memory_file(
        memory_dir / "release-note.md",
        memory_type="project",
        description="Release branch freeze",
        body="Merge freeze starts Thursday for the mobile release branch.",
    )

    result = retrieve_relevant_memories("release freeze", [memory_dir], max_tokens=200)

    assert path.exists()
    assert result.memories
    assert result.memories[0].path.name == "release-note.md"


def test_recall_skips_malformed_memory_file(tmp_path):
    memory_dir = tmp_path / "memories"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "broken.md").write_text("---\ntype: [\n---\nbody", encoding="utf-8")

    result = retrieve_relevant_memories("body", [memory_dir], max_tokens=200)
    assert result.memories == []
