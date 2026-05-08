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

from koder_agent.harness.permissions.shell_classifier import classify_shell_command


def test_shell_classifier_rejects_destructive_rm_rf():
    result = classify_shell_command("rm -rf /")
    assert result.allowed is False
    assert result.requires_approval is True
    assert result.destructive is True


def test_shell_classifier_marks_read_only_rg_pipeline():
    result = classify_shell_command('rg "TODO" src | head -5')
    assert result.allowed is True
    assert result.read_only is True
    assert result.requires_approval is False


def test_shell_classifier_marks_touch_as_write():
    result = classify_shell_command("touch foo.txt")
    assert result.allowed is True
    assert result.read_only is False
    assert result.requires_approval is True


def test_shell_classifier_rejects_malformed_empty_command():
    result = classify_shell_command("   ")
    assert result.allowed is False
    assert result.malformed is True


def test_shell_classifier_flags_interpreter_prefix_as_dangerous():
    result = classify_shell_command('python -c "print(1)"')
    assert result.allowed is False
    assert result.destructive is True
