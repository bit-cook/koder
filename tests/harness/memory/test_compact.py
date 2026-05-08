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

from koder_agent.harness.memory.compact import compact_messages


def test_compact_messages_returns_summary_and_kept_tail():
    messages = [{"role": "user", "content": f"message {index}"} for index in range(20)]
    result = compact_messages(messages, max_messages=5)
    assert result.summary is not None
    assert len(result.kept_messages) <= 5
    assert result.original_count == 20


def test_compact_respects_budget_tokens():
    messages = [{"role": "user", "content": "token heavy " * 20} for _ in range(50)]
    result = compact_messages(messages, max_tokens=100)
    assert result.token_count <= 100


def test_compact_keeps_messages_when_under_budget():
    messages = [{"role": "user", "content": "short"} for _ in range(3)]
    result = compact_messages(messages, max_messages=5, max_tokens=1000)
    assert result.summary is None
    assert result.kept_messages == messages
