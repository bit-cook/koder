"""Tests for _reconcile_tool_pairs in AgentScheduler.

M1 fix: after filtering unreplayable items, orphaned function_call or
function_call_output items (whose counterpart was removed) must be stripped
to avoid provider 400 errors.
"""

import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.memory.compact import replayable_session_items


class TestReconcileToolPairs:
    """Unit tests for AgentScheduler._reconcile_tool_pairs."""

    def test_paired_items_kept(self):
        """Items with matching call_id on both sides survive."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        assert result == items

    def test_orphaned_call_removed(self):
        """A function_call without a matching function_call_output is dropped."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            # No corresponding function_call_output for call_1
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        assert len(result) == 2
        assert all(item.get("type") != "function_call" for item in result)

    def test_orphaned_output_removed(self):
        """A function_call_output without a matching function_call is dropped."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            # No corresponding function_call for call_2
            {"type": "function_call_output", "call_id": "call_2", "output": "ok"},
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        assert len(result) == 2
        assert all(item.get("type") != "function_call_output" for item in result)

    def test_mixed_paired_and_orphaned(self):
        """Only the orphaned half is removed; paired items stay."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "call_id": "call_1", "name": "read_file", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "file_content"},
            {"type": "function_call", "call_id": "call_2", "name": "write_file", "arguments": "{}"},
            # call_2 output was removed by replayable filter
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        # call_2 (orphaned call) should be removed; everything else stays
        assert len(result) == 4
        call_ids = [item.get("call_id") for item in result if item.get("call_id")]
        assert "call_1" in call_ids
        assert "call_2" not in call_ids

    def test_non_tool_items_always_kept(self):
        """Messages and other item types are never touched."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "message", "role": "assistant", "content": "hello"},
            {"type": "reasoning", "content": "thinking..."},
        ]
        result = replayable_session_items(items)
        assert result == items

    def test_empty_list(self):
        """Empty input returns empty output."""
        assert replayable_session_items([]) == []

    def test_items_without_call_id_are_dropped(self):
        """function_call items without call_id cannot be paired; they are dropped."""
        items = [
            {"type": "message", "role": "user", "content": "hi"},
            {"type": "function_call", "name": "bad_item"},  # no call_id
            {"type": "message", "role": "assistant", "content": "done"},
        ]
        result = replayable_session_items(items)
        assert len(result) == 2
        assert all(item.get("type") != "function_call" for item in result)

    def test_multiple_paired_calls(self):
        """Multiple complete pairs all survive."""
        items = [
            {"type": "function_call", "call_id": "a", "name": "tool_a", "arguments": "{}"},
            {"type": "function_call", "call_id": "b", "name": "tool_b", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "a", "output": "out_a"},
            {"type": "function_call_output", "call_id": "b", "output": "out_b"},
        ]
        result = replayable_session_items(items)
        assert len(result) == 4
