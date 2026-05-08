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

from koder_agent.harness.memory.pruning import prune_memories


def test_prune_memories_removes_stale_entries():
    result = prune_memories([{"age_days": 999}, {"age_days": 1}], max_age_days=30)
    assert len(result.kept) == 1
    assert len(result.removed) == 1
