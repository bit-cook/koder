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

from koder_agent.harness.permissions.path_policy import (
    DANGEROUS_DELETE_PATHS,
    DANGEROUS_DIRECTORIES,
    DANGEROUS_FILES,
    evaluate_path_access,
    has_shell_expansion_syntax,
    resolve_with_symlinks,
)


# Shell expansion blocking tests
def test_blocks_dollar_var():
    result = evaluate_path_access("$HOME/file.txt", operation="write")
    assert result.allowed is False
    assert "shell expansion" in result.reason.lower()


def test_blocks_dollar_brace_var():
    result = evaluate_path_access("${HOME}/file.txt", operation="write")
    assert result.allowed is False
    assert "shell expansion" in result.reason.lower()


def test_blocks_dollar_paren_cmd():
    result = evaluate_path_access("$(whoami)/file.txt", operation="write")
    assert result.allowed is False
    assert "shell expansion" in result.reason.lower()


def test_blocks_backticks():
    result = evaluate_path_access("`date`/file.txt", operation="write")
    assert result.allowed is False
    assert "shell expansion" in result.reason.lower()


def test_blocks_percent_var():
    result = evaluate_path_access("%TEMP%/file.txt", operation="write")
    assert result.allowed is False
    assert "shell expansion" in result.reason.lower()


def test_blocks_equals_command():
    result = evaluate_path_access("=rg", operation="write")
    assert result.allowed is False
    assert "shell expansion" in result.reason.lower()


def test_blocks_tilde_user():
    result = evaluate_path_access("~root/file.txt", operation="write")
    assert result.allowed is False
    assert "shell expansion" in result.reason.lower()


def test_blocks_tilde_plus():
    result = evaluate_path_access("~+/file.txt", operation="write")
    assert result.allowed is False
    assert "shell expansion" in result.reason.lower()


def test_blocks_tilde_minus():
    result = evaluate_path_access("~-/file.txt", operation="write")
    assert result.allowed is False
    assert "shell expansion" in result.reason.lower()


def test_allows_plain_tilde(tmp_path):
    result = evaluate_path_access("~", operation="read", workspace_root=tmp_path)
    # Will be resolved to home directory, outside workspace - but no shell expansion error
    assert "shell expansion" not in result.reason.lower()


def test_allows_tilde_slash(tmp_path):
    result = evaluate_path_access("~/file.txt", operation="read", workspace_root=tmp_path)
    # Will be resolved to home directory, outside workspace - but no shell expansion error
    assert "shell expansion" not in result.reason.lower()


def test_normal_paths_still_work(tmp_path):
    target = tmp_path / "normal.txt"
    result = evaluate_path_access(str(target), operation="read", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is False


# has_shell_expansion_syntax utility tests
def test_has_shell_expansion_syntax_dollar():
    assert has_shell_expansion_syntax("$HOME") is True
    assert has_shell_expansion_syntax("${VAR}") is True
    assert has_shell_expansion_syntax("$(cmd)") is True


def test_has_shell_expansion_syntax_percent():
    assert has_shell_expansion_syntax("%TEMP%") is True


def test_has_shell_expansion_syntax_backtick():
    assert has_shell_expansion_syntax("`date`") is True


def test_has_shell_expansion_syntax_equals():
    assert has_shell_expansion_syntax("=rg") is True


def test_has_shell_expansion_syntax_tilde():
    assert has_shell_expansion_syntax("~root") is True
    assert has_shell_expansion_syntax("~+") is True
    assert has_shell_expansion_syntax("~-") is True
    assert has_shell_expansion_syntax("~") is False
    assert has_shell_expansion_syntax("~/") is False
    assert has_shell_expansion_syntax("~/file") is False


def test_has_shell_expansion_syntax_normal():
    assert has_shell_expansion_syntax("/normal/path/file.txt") is False
    assert has_shell_expansion_syntax("relative/path") is False


# Symlink resolution tests
def test_symlink_inside_workspace_pointing_outside_write_blocked(tmp_path):
    # Create a symlink inside workspace pointing to /etc/passwd
    link_path = tmp_path / "secret_link"
    link_path.symlink_to("/etc/passwd")

    result = evaluate_path_access(str(link_path), operation="write", workspace_root=tmp_path)
    assert result.allowed is False
    assert result.reason == "symlink resolves outside workspace"


def test_symlink_inside_workspace_pointing_outside_read_blocked(tmp_path):
    # Create a symlink inside workspace pointing to /etc/passwd
    link_path = tmp_path / "secret_link"
    link_path.symlink_to("/etc/passwd")

    result = evaluate_path_access(str(link_path), operation="read", workspace_root=tmp_path)
    assert result.allowed is False
    assert result.reason == "symlink resolves outside workspace"


def test_symlink_within_workspace_allowed(tmp_path):
    # Create a real file in workspace
    real_file = tmp_path / "real.txt"
    real_file.write_text("content", encoding="utf-8")

    # Create a symlink to it
    link_path = tmp_path / "link.txt"
    link_path.symlink_to(real_file)

    result = evaluate_path_access(str(link_path), operation="write", workspace_root=tmp_path)
    assert result.allowed is True


def test_resolve_with_symlinks_utility(tmp_path):
    # Create a real file
    real_file = tmp_path / "real.txt"
    real_file.write_text("content", encoding="utf-8")

    # Create a symlink to it
    link_path = tmp_path / "link.txt"
    link_path.symlink_to(real_file)

    original, real = resolve_with_symlinks(str(link_path))
    # Both should resolve to the real file
    assert Path(original).resolve() == real_file.resolve()
    assert Path(real).resolve() == real_file.resolve()


# Dangerous files/directories constant tests
def test_dangerous_files_constant():
    expected_files = {
        ".gitconfig",
        ".gitmodules",
        ".bashrc",
        ".bash_profile",
        ".zshrc",
        ".zprofile",
        ".profile",
        ".ripgreprc",
    }
    assert expected_files.issubset(DANGEROUS_FILES)


def test_dangerous_directories_constant():
    expected_dirs = {".git", ".vscode", ".idea", ".koder"}
    assert expected_dirs.issubset(DANGEROUS_DIRECTORIES)


def test_dangerous_delete_paths_includes_home():
    assert Path.home() in DANGEROUS_DELETE_PATHS


# Dangerous file/directory protection tests
def test_write_to_bashrc_requires_approval(tmp_path):
    bashrc = tmp_path / ".bashrc"
    result = evaluate_path_access(str(bashrc), operation="write", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is True
    assert "dangerous" in result.reason.lower()


def test_write_to_gitconfig_requires_approval(tmp_path):
    gitconfig = tmp_path / ".gitconfig"
    result = evaluate_path_access(str(gitconfig), operation="write", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is True
    assert "dangerous" in result.reason.lower()


def test_write_inside_git_dir_requires_approval(tmp_path):
    git_file = tmp_path / ".git" / "config"
    result = evaluate_path_access(str(git_file), operation="write", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is True
    assert "dangerous" in result.reason.lower()


def test_write_inside_koder_dir_requires_approval(tmp_path):
    koder_file = tmp_path / ".koder" / "config.yaml"
    result = evaluate_path_access(str(koder_file), operation="write", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is True
    assert "dangerous" in result.reason.lower()


def test_write_inside_vscode_dir_requires_approval(tmp_path):
    vscode_file = tmp_path / ".vscode" / "settings.json"
    result = evaluate_path_access(str(vscode_file), operation="write", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is True
    assert "dangerous" in result.reason.lower()


def test_delete_dangerous_file_requires_approval(tmp_path):
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text("# shell config", encoding="utf-8")
    result = evaluate_path_access(str(bashrc), operation="delete", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is True
    assert "dangerous" in result.reason.lower()


def test_read_dangerous_file_allowed(tmp_path):
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text("# shell config", encoding="utf-8")
    result = evaluate_path_access(str(bashrc), operation="read", workspace_root=tmp_path)
    assert result.allowed is True
    # Read should still be allowed without approval for workspace files
    assert result.requires_approval is False


def test_write_to_normal_file_in_workspace(tmp_path):
    normal_file = tmp_path / "notes.txt"
    result = evaluate_path_access(str(normal_file), operation="write", workspace_root=tmp_path)
    assert result.allowed is True
    # Normal writes still require approval (existing behavior)
    assert result.requires_approval is True
    # But not because it's dangerous
    assert "dangerous" not in result.reason.lower()


def test_nested_dangerous_directory(tmp_path):
    # File deep inside .git directory
    nested_file = tmp_path / ".git" / "objects" / "pack" / "data"
    result = evaluate_path_access(str(nested_file), operation="write", workspace_root=tmp_path)
    assert result.allowed is True
    assert result.requires_approval is True
    assert "dangerous" in result.reason.lower()
