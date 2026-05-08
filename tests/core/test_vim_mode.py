"""Tests for vim mode support."""

from koder_agent.core.vim_mode import VimModeManager


def test_default_disabled():
    mgr = VimModeManager()
    assert not mgr.enabled


def test_enable():
    mgr = VimModeManager()
    mgr.enable()
    assert mgr.enabled


def test_disable():
    mgr = VimModeManager()
    mgr.enable()
    mgr.disable()
    assert not mgr.enabled


def test_toggle():
    mgr = VimModeManager()
    mgr.toggle()
    assert mgr.enabled
    mgr.toggle()
    assert not mgr.enabled


def test_get_editing_mode_emacs():
    from prompt_toolkit.enums import EditingMode

    mgr = VimModeManager()
    assert mgr.get_editing_mode() == EditingMode.EMACS


def test_get_editing_mode_vi():
    from prompt_toolkit.enums import EditingMode

    mgr = VimModeManager()
    mgr.enable()
    assert mgr.get_editing_mode() == EditingMode.VI


def test_status_text_disabled():
    mgr = VimModeManager()
    assert mgr.get_status_text() == ""


def test_status_text_enabled():
    mgr = VimModeManager()
    mgr.enable()
    # Should show some mode indicator
    status = mgr.get_status_text()
    assert len(status) > 0


def test_persist_and_load(tmp_path):
    """Vim mode state should be persistable."""
    mgr = VimModeManager(state_path=tmp_path / "vim_state.json")
    mgr.enable()
    mgr.save()

    mgr2 = VimModeManager(state_path=tmp_path / "vim_state.json")
    mgr2.load()
    assert mgr2.enabled


def test_load_nonexistent(tmp_path):
    """Loading from nonexistent file should not crash."""
    mgr = VimModeManager(state_path=tmp_path / "nonexistent.json")
    mgr.load()
    assert not mgr.enabled
