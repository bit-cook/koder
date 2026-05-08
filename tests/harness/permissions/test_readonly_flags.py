"""Tests for read-only command flag-level validation."""

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
    is_readonly_git_subcommand,
)


class TestGitReadOnlySubcommands:
    # git log
    def test_git_log_plain(self):
        assert classify_shell_command("git log").read_only

    def test_git_log_oneline(self):
        assert classify_shell_command("git log --oneline").read_only

    def test_git_log_format(self):
        assert classify_shell_command("git log --format='%H %s'").read_only

    def test_git_log_graph(self):
        assert classify_shell_command("git log --graph --decorate").read_only

    def test_git_log_n(self):
        assert classify_shell_command("git log -n 10").read_only

    def test_git_log_author(self):
        assert classify_shell_command("git log --author='John'").read_only

    # git diff
    def test_git_diff_plain(self):
        assert classify_shell_command("git diff").read_only

    def test_git_diff_staged(self):
        assert classify_shell_command("git diff --staged").read_only

    def test_git_diff_cached(self):
        assert classify_shell_command("git diff --cached").read_only

    def test_git_diff_stat(self):
        assert classify_shell_command("git diff --stat").read_only

    def test_git_diff_name_only(self):
        assert classify_shell_command("git diff --name-only").read_only

    def test_git_diff_head(self):
        assert classify_shell_command("git diff HEAD~3..HEAD").read_only

    # git status
    def test_git_status_plain(self):
        assert classify_shell_command("git status").read_only

    def test_git_status_short(self):
        assert classify_shell_command("git status -s").read_only

    def test_git_status_porcelain(self):
        assert classify_shell_command("git status --porcelain").read_only

    # git show
    def test_git_show_plain(self):
        assert classify_shell_command("git show HEAD").read_only

    def test_git_show_stat(self):
        assert classify_shell_command("git show --stat HEAD").read_only

    # git branch (read-only)
    def test_git_branch_list(self):
        assert classify_shell_command("git branch").read_only

    def test_git_branch_list_all(self):
        assert classify_shell_command("git branch -a").read_only

    def test_git_branch_list_remote(self):
        assert classify_shell_command("git branch -r").read_only

    def test_git_branch_verbose(self):
        assert classify_shell_command("git branch -v").read_only

    # git branch (write -- NOT read-only)
    def test_git_branch_delete_not_readonly(self):
        assert not classify_shell_command("git branch -d feature").read_only

    def test_git_branch_force_delete_not_readonly(self):
        assert not classify_shell_command("git branch -D feature").read_only

    def test_git_branch_move_not_readonly(self):
        assert not classify_shell_command("git branch -m old new").read_only

    def test_git_branch_create_not_readonly(self):
        assert not classify_shell_command("git branch new-branch").read_only

    # git rev-parse
    def test_git_rev_parse(self):
        assert classify_shell_command("git rev-parse HEAD").read_only

    def test_git_rev_parse_short(self):
        assert classify_shell_command("git rev-parse --short HEAD").read_only

    # git stash
    def test_git_stash_list_readonly(self):
        assert classify_shell_command("git stash list").read_only

    def test_git_stash_show_readonly(self):
        assert classify_shell_command("git stash show").read_only

    def test_git_stash_pop_not_readonly(self):
        assert not classify_shell_command("git stash pop").read_only

    def test_git_stash_drop_not_readonly(self):
        assert not classify_shell_command("git stash drop").read_only

    # Write commands
    def test_git_add_not_readonly(self):
        assert not classify_shell_command("git add .").read_only

    def test_git_commit_not_readonly(self):
        assert not classify_shell_command("git commit -m 'msg'").read_only

    def test_git_push_not_readonly(self):
        assert not classify_shell_command("git push origin main").read_only

    def test_git_reset_not_readonly(self):
        assert not classify_shell_command("git reset --hard HEAD~1").read_only

    def test_git_checkout_not_readonly(self):
        assert not classify_shell_command("git checkout -- file.py").read_only

    def test_git_rebase_not_readonly(self):
        assert not classify_shell_command("git rebase main").read_only

    def test_git_merge_not_readonly(self):
        assert not classify_shell_command("git merge feature").read_only

    def test_git_clean_not_readonly(self):
        assert not classify_shell_command("git clean -fd").read_only


class TestHelperFunction:
    def test_log_is_readonly(self):
        assert is_readonly_git_subcommand(["git", "log"])

    def test_diff_staged_is_readonly(self):
        assert is_readonly_git_subcommand(["git", "diff", "--staged"])

    def test_branch_list_flags_are_readonly(self):
        assert is_readonly_git_subcommand(["git", "branch", "-a", "-v"])

    def test_branch_delete_is_not_readonly(self):
        assert not is_readonly_git_subcommand(["git", "branch", "-d", "feature"])

    def test_stash_list_is_readonly(self):
        assert is_readonly_git_subcommand(["git", "stash", "list"])

    def test_stash_pop_is_not_readonly(self):
        assert not is_readonly_git_subcommand(["git", "stash", "pop"])
