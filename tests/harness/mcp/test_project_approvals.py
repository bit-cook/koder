import json
import os
import stat
import subprocess
import sys
import threading

import pytest

import koder_agent.mcp.project_approvals as approvals_module
from koder_agent.mcp.project_approvals import (
    is_project_source_approved,
    reset_project_choices,
    set_project_approval,
)


def test_no_approvals_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert (
        is_project_source_approved(
            project_root="/some/project",
            source_path="/some/project/.mcp.json",
            source_digest="digest",
        )
        is None
    )


def test_set_and_check_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    root = tmp_path / "project"
    source = tmp_path / "project" / ".mcp.json"
    set_project_approval(
        project_root=root,
        source_path=source,
        source_digest="digest-a",
        approved=True,
    )
    assert _approval(root, source, "digest-a") is True
    assert _approval(root, source, "digest-b") is None
    assert _approval(tmp_path / "other", source, "digest-a") is None


def test_set_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    root = tmp_path / "project"
    source = tmp_path / "project" / ".mcp.json"
    set_project_approval(
        project_root=root,
        source_path=source,
        source_digest="digest-a",
        approved=False,
    )
    assert _approval(root, source, "digest-a") is False


def test_reset_clears_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    root = tmp_path / "project"
    source = tmp_path / "project" / ".mcp.json"
    set_project_approval(
        project_root=root,
        source_path=source,
        source_digest="digest-a",
        approved=True,
    )
    count = reset_project_choices()
    assert count == 1
    assert _approval(root, source, "digest-a") is None


def test_reset_specific_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    root1 = tmp_path / "p1"
    root2 = tmp_path / "p2"
    source1 = tmp_path / "p1" / ".mcp.json"
    source2 = tmp_path / "p2" / ".mcp.json"
    set_project_approval(
        project_root=root1,
        source_path=source1,
        source_digest="digest-1",
        approved=True,
    )
    set_project_approval(
        project_root=root2,
        source_path=source2,
        source_digest="digest-2",
        approved=True,
    )
    count = reset_project_choices(root1)
    assert count == 1
    assert _approval(root1, source1, "digest-1") is None
    assert _approval(root2, source2, "digest-2") is True


def test_reset_nonexistent_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    set_project_approval(
        project_root=tmp_path / "p1",
        source_path=tmp_path / "p1" / ".mcp.json",
        source_digest="digest-1",
        approved=True,
    )
    count = reset_project_choices(tmp_path / "nonexistent")
    assert count == 0


def test_reset_empty_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    count = reset_project_choices()
    assert count == 0


def test_old_three_argument_set_form_raises_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "project" / ".mcp.json"

    with pytest.raises(TypeError, match="positional argument"):
        set_project_approval(source, "digest-a", True)


def test_legacy_path_only_approval_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    approvals_path = tmp_path / ".koder" / "mcp-project-approvals.json"
    approvals_path.parent.mkdir()
    source = tmp_path / "project" / ".mcp.json"
    approvals_path.write_text(
        json.dumps({str((tmp_path / "project").resolve()): True}),
        encoding="utf-8",
    )

    assert _approval(tmp_path / "project", source, "digest-a") is None
    assert reset_project_choices(tmp_path / "project") == 1


def test_version_two_source_approval_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    approvals_path = tmp_path / ".koder" / "mcp-project-approvals.json"
    approvals_path.parent.mkdir()
    root = tmp_path / "project"
    source = root / ".mcp.json"
    approvals_path.write_text(
        json.dumps(
            {
                "version": 2,
                "sources": {str(source.resolve()): {"approved": True, "digest": "digest-a"}},
                "legacy": {},
            }
        ),
        encoding="utf-8",
    )

    assert _approval(root, source, "digest-a") is None
    assert reset_project_choices(root) == 1


def test_reset_cannot_be_resurrected_by_stale_concurrent_writer(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "project"
    source = root / ".mcp.json"
    set_project_approval(
        project_root=root,
        source_path=source,
        source_digest="digest-a",
        approved=True,
    )
    setter_loaded = threading.Event()
    resetter_loaded = threading.Event()
    release_setter = threading.Event()
    real_load = approvals_module._load_approvals_unlocked

    class NoopProcessLock:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(approvals_module, "_PROCESS_LOCK", NoopProcessLock())

    def delayed_load():
        loaded = real_load()
        if threading.current_thread().name == "approval-setter":
            setter_loaded.set()
            assert release_setter.wait(timeout=5)
        elif threading.current_thread().name == "approval-resetter":
            resetter_loaded.set()
        return loaded

    monkeypatch.setattr(approvals_module, "_load_approvals_unlocked", delayed_load)
    setter = threading.Thread(
        name="approval-setter",
        target=set_project_approval,
        kwargs={
            "project_root": root,
            "source_path": root / "other.mcp.json",
            "source_digest": "digest-b",
            "approved": True,
        },
    )
    resetter = threading.Thread(
        name="approval-resetter",
        target=reset_project_choices,
        args=(root,),
    )

    setter.start()
    assert setter_loaded.wait(timeout=5)
    resetter.start()
    assert not resetter_loaded.wait(timeout=0.2)
    release_setter.set()
    setter.join(timeout=5)
    resetter.join(timeout=5)

    assert not setter.is_alive()
    assert not resetter.is_alive()
    assert _approval(root, source, "digest-a") is None
    assert _approval(root, root / "other.mcp.json", "digest-b") is None


def test_eight_cross_process_writers_preserve_every_record_and_private_mode(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "project"
    root.mkdir()
    child_code = """
import sys
from koder_agent.mcp.project_approvals import set_project_approval

set_project_approval(
    project_root=sys.argv[1],
    source_path=sys.argv[2],
    source_digest=sys.argv[3],
    approved=True,
)
"""
    env = os.environ.copy()
    env["HOME"] = str(home)
    processes = []
    for index in range(8):
        source = root / f"server-{index}.mcp.json"
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    str(root),
                    str(source),
                    f"digest-{index}",
                ],
                cwd=tmp_path,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    failures = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        if process.returncode != 0:
            failures.append((process.returncode, stdout, stderr))
    assert failures == []

    monkeypatch.setenv("HOME", str(home))
    for index in range(8):
        assert (
            _approval(
                root,
                root / f"server-{index}.mcp.json",
                f"digest-{index}",
            )
            is True
        )

    approvals_path = home / ".koder" / "mcp-project-approvals.json"
    assert stat.S_IMODE(approvals_path.stat().st_mode) == 0o600


def _approval(root, source, digest):
    return is_project_source_approved(
        project_root=root,
        source_path=source,
        source_digest=digest,
    )
