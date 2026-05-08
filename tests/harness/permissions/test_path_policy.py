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

from koder_agent.harness.permissions.path_policy import evaluate_path_access


def test_path_policy_blocks_outside_workspace_delete():
    result = evaluate_path_access("/tmp", operation="delete")
    assert result.allowed is False
    assert result.requires_approval is True


def test_path_policy_allows_workspace_read(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    result = evaluate_path_access(str(target), operation="read", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is False


def test_path_policy_requires_approval_for_workspace_write(tmp_path):
    target = tmp_path / "notes.txt"
    result = evaluate_path_access(str(target), operation="write", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is True


def test_path_policy_rejects_path_traversal(tmp_path):
    escaped = tmp_path / ".." / "outside.txt"
    result = evaluate_path_access(str(escaped), operation="write", workspace_root=tmp_path)
    assert result.allowed is False
    assert result.reason == "path escapes workspace"
