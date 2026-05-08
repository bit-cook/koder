from koder_agent.mcp.project_approvals import (
    is_project_approved,
    reset_project_choices,
    set_project_approval,
)


def test_no_approvals_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert is_project_approved("/some/project") is None


def test_set_and_check_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    set_project_approval(tmp_path / "project", True)
    assert is_project_approved(tmp_path / "project") is True


def test_set_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    set_project_approval(tmp_path / "project", False)
    assert is_project_approved(tmp_path / "project") is False


def test_reset_clears_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    set_project_approval(tmp_path / "project", True)
    count = reset_project_choices()
    assert count == 1
    assert is_project_approved(tmp_path / "project") is None


def test_reset_specific_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    set_project_approval(tmp_path / "p1", True)
    set_project_approval(tmp_path / "p2", True)
    count = reset_project_choices(tmp_path / "p1")
    assert count == 1
    assert is_project_approved(tmp_path / "p1") is None
    assert is_project_approved(tmp_path / "p2") is True


def test_reset_nonexistent_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".koder").mkdir()
    set_project_approval(tmp_path / "p1", True)
    count = reset_project_choices(tmp_path / "nonexistent")
    assert count == 0


def test_reset_empty_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    count = reset_project_choices()
    assert count == 0
