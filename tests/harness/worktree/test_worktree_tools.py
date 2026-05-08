"""Tests for EnterWorktree and ExitWorktree tools."""

import json
import subprocess
from pathlib import Path

import pytest

from koder_agent.tools.worktree import (
    _get_worktree_session,
    _set_worktree_session,
    enter_worktree,
    exit_worktree,
)


@pytest.fixture()
def git_repo(tmp_path):
    """Create a minimal git repo for worktree tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
    return repo


@pytest.fixture(autouse=True)
def _clear_session():
    _set_worktree_session(None)
    yield
    _set_worktree_session(None)


def test_enter_worktree_creates_dir(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    result = json.loads(enter_worktree(name="test-feature"))
    assert "worktree_path" in result
    assert Path(result["worktree_path"]).exists()


def test_enter_worktree_rejects_double_entry(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    enter_worktree(name="first")
    result = json.loads(enter_worktree(name="second"))
    assert "already in a worktree" in result["message"].lower()


def test_exit_worktree_keep(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    enter_worktree(name="feat")
    result = json.loads(exit_worktree(action="keep"))
    assert result["action"] == "keep"
    assert _get_worktree_session() is None


def test_exit_worktree_without_enter():
    result = json.loads(exit_worktree(action="keep"))
    assert "no active" in result["message"].lower()


def test_exit_worktree_remove(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    enter_result = json.loads(enter_worktree(name="removable"))
    _wt_path = Path(enter_result["worktree_path"])  # noqa: F841
    result = json.loads(exit_worktree(action="remove", discard_changes=True))
    assert result["action"] == "remove"


def test_slug_validation():
    result = json.loads(enter_worktree(name="../escape"))
    assert (
        "invalid" in result.get("message", "").lower()
        or "error" in result.get("message", "").lower()
    )
