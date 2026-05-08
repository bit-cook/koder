import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.worktree.conflicts import detect_conflict
from koder_agent.harness.worktree.service import WorktreeService


def _init_git_repo(repo_root: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
    (repo_root / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "README.md"], cwd=repo_root, check=True, capture_output=True, text=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )


def test_worktree_conflict_blocks_transition():
    result = detect_conflict(
        active_task_id="t1",
        requested_task_id="t2",
        active_session_id="s1",
        requested_session_id="s2",
    )
    assert result.blocked is True


def test_worktree_service_creates_enters_and_exits_git_worktree(tmp_path):
    service = WorktreeService.for_test(tmp_path)
    created = service.create("feature/demo")
    entered = service.enter(created.path)
    exited = service.exit(created.path)
    assert entered.ok is True and exited.ok is True


def test_worktree_service_creates_real_git_worktree_for_repo(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_git_repo(repo_root)

    service = WorktreeService(repo_root / ".koder" / "worktrees", repo_root=repo_root)
    created = service.create("feature/demo")

    assert created.path.exists()
    assert created.branch == "feature/demo"
    assert (created.path / ".git").exists()
