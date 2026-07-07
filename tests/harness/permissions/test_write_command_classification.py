"""Write-capable commands must not be misclassified as read-only.

Covers four confirmed auto-run bypass holes:

1. ``sort -o FILE`` / ``sort --output=FILE`` overwrites FILE.
2. ``find`` file-writing actions ``-fls`` and ``-fprint0`` (in addition to the
   already-covered ``-fprint``/``-fprintf``).
3. Read-only git subcommands (log/show/diff) with ``--output[=]FILE`` (or a
   stray ``-o``) write/truncate an arbitrary path.
4. fd-prefixed redirects (``1>``, ``2>``, ``2>>``) truncate/append files but
   were skipped by the write-redirection regex.
"""

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

from koder_agent.harness.permissions.shell_classifier import (  # noqa: E402
    classify_shell_command,
)


class TestSortOutputFlagIsWrite:
    def test_sort_dash_o_is_not_read_only(self):
        result = classify_shell_command("sort -o /tmp/x payload.txt")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_sort_dash_o_attached_is_not_read_only(self):
        result = classify_shell_command("sort -o/tmp/x payload.txt")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_sort_long_output_is_not_read_only(self):
        result = classify_shell_command("sort --output=/tmp/x payload.txt")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_sort_long_output_separate_arg_is_not_read_only(self):
        result = classify_shell_command("sort --output /tmp/x payload.txt")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_sort_clustered_short_flags_with_o_is_not_read_only(self):
        result = classify_shell_command("sort -uo /tmp/x payload.txt")
        assert result.read_only is False
        assert result.requires_approval is True


class TestFindFileWritingActions:
    def test_find_fls_is_not_read_only(self):
        result = classify_shell_command("find . -name x -fls /tmp/x")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_find_fprint0_is_not_read_only(self):
        result = classify_shell_command("find . -fprint0 /tmp/x")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_find_fprint_is_not_read_only(self):
        result = classify_shell_command("find . -fprint /tmp/x")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_find_fprintf_is_not_read_only(self):
        result = classify_shell_command("find . -fprintf /tmp/x '%p'")
        assert result.read_only is False
        assert result.requires_approval is True


class TestGitOutputFlagIsWrite:
    def test_git_log_output_equals_is_not_read_only(self):
        result = classify_shell_command("git log --output=/tmp/leak.txt")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_git_log_output_separate_arg_is_not_read_only(self):
        result = classify_shell_command("git log --output /tmp/leak.txt")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_git_diff_output_is_not_read_only(self):
        result = classify_shell_command("git diff --output=leak.txt")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_git_show_output_is_not_read_only(self):
        result = classify_shell_command("git show --output=/tmp/x HEAD")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_git_log_dash_o_is_not_read_only(self):
        result = classify_shell_command("git log -o /tmp/x")
        assert result.read_only is False
        assert result.requires_approval is True


class TestNumberedFdRedirectIsWrite:
    def test_stdout_fd_redirect_is_not_read_only(self):
        result = classify_shell_command("echo hi 1> /tmp/x")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_stderr_fd_redirect_is_not_read_only(self):
        result = classify_shell_command("cat y 2> /tmp/x")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_stderr_fd_append_redirect_is_not_read_only(self):
        result = classify_shell_command("echo hi 2>> /tmp/x")
        assert result.read_only is False
        assert result.requires_approval is True

    def test_fd_redirect_without_space_is_not_read_only(self):
        result = classify_shell_command("cat y 2>/tmp/x")
        assert result.read_only is False
        assert result.requires_approval is True


class TestReadOnlyRegressions:
    """Legitimate read-only invocations must stay auto-runnable."""

    def test_plain_sort_stays_read_only(self):
        result = classify_shell_command("sort file.txt")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_sort_with_non_output_flags_stays_read_only(self):
        result = classify_shell_command("sort -u -n file.txt")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_plain_find_stays_read_only(self):
        result = classify_shell_command("find . -name x")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_git_log_stays_read_only(self):
        result = classify_shell_command("git log")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_git_log_oneline_stays_read_only(self):
        result = classify_shell_command("git log --oneline")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_ls_stays_read_only(self):
        result = classify_shell_command("ls")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_cat_stays_read_only(self):
        result = classify_shell_command("cat file")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_grep_stays_read_only(self):
        result = classify_shell_command("grep x file")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_fd_duplication_stays_read_only(self):
        """``2>&1`` duplicates a file descriptor; it does not write a file."""
        result = classify_shell_command("grep x file 2>&1")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_dev_null_redirect_stays_read_only(self):
        result = classify_shell_command("ls > /dev/null")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False

    def test_stderr_to_dev_null_stays_read_only(self):
        result = classify_shell_command("find . -name x 2>/dev/null")
        assert result.allowed is True
        assert result.read_only is True
        assert result.requires_approval is False
