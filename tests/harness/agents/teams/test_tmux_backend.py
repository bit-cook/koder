"""Tests for tmux team backend."""

from unittest.mock import MagicMock, patch

from koder_agent.harness.agents.teams.tmux_backend import (
    TmuxBackend,
    TmuxPane,
    get_current_tmux_session_name,
    get_tmux_session_name,
    is_tmux_available,
)


def test_is_tmux_available_with_tmux():
    with patch("shutil.which", return_value="/usr/bin/tmux"):
        assert is_tmux_available()


def test_is_tmux_available_without_tmux():
    with patch("shutil.which", return_value=None):
        assert not is_tmux_available()


def test_tmux_pane_dataclass():
    pane = TmuxPane(
        pane_id="%1",
        session_name="koder-team",
        member_name="worker-1",
        pid=12345,
    )
    assert pane.pane_id == "%1"
    assert pane.member_name == "worker-1"


def test_get_tmux_session_name():
    name = get_tmux_session_name("my-team")
    assert "my-team" in name
    assert isinstance(name, str)


def test_get_current_tmux_session_name_skips_outside_tmux():
    assert get_current_tmux_session_name({}) is None


@patch("subprocess.run")
def test_get_current_tmux_session_name_reads_tmux_session(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="leader\n")

    assert get_current_tmux_session_name({"TMUX": "/tmp/tmux-socket"}) == "leader"

    cmd = mock_run.call_args[0][0]
    assert cmd == ["tmux", "display-message", "-p", "#S"]


class TestTmuxBackend:
    @patch("subprocess.run")
    def test_spawn_member(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="%1\n"),
        ]

        backend = TmuxBackend(session_name="test-team")
        pane = backend.spawn_member(
            name="worker-1",
            prompt="Fix the auth bug",
            cwd="/tmp/workspace",
        )
        assert pane.member_name == "worker-1"
        mock_run.assert_called()
        # Should have called tmux split-window or new-window
        cmd = mock_run.call_args_list[-1][0][0]
        assert "tmux" in cmd[0] if isinstance(cmd, list) else "tmux" in cmd

    @patch("subprocess.run")
    def test_spawn_builds_koder_command(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="%1\n"),
        ]

        backend = TmuxBackend(session_name="test-team")
        backend.spawn_member(
            name="worker-1",
            prompt="Fix bug",
            cwd="/tmp/workspace",
        )
        # The command should re-enter the current Python package.
        call_args = str(mock_run.call_args_list)
        assert "-m koder_agent.cli" in call_args

    @patch("subprocess.run")
    def test_spawn_inherits_runtime_env_and_model(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="%1\n"),
        ]

        backend = TmuxBackend(session_name="test-team")
        backend.spawn_member(
            name="worker-1",
            prompt="/model",
            cwd="/tmp/workspace",
            model="gpt-test",
            env={
                "HOME": "/tmp/home",
                "KODER_BASE_URL": "http://localhost:9999/v1",
                "KODER_MODEL": "old-model",
                "PYTHONPATH": "/tmp/source",
                "UNRELATED_SECRET": "do-not-forward",
            },
        )

        command = mock_run.call_args_list[2][0][0][-1]
        assert "HOME=/tmp/home" in command
        assert "KODER_BASE_URL=http://localhost:9999/v1" in command
        assert "KODER_MODEL=gpt-test" in command
        assert "PYTHONPATH=/tmp/source" in command
        assert "UNRELATED_SECRET" not in command
        assert "--model" not in command

    @patch("subprocess.run")
    def test_spawn_keeps_existing_tmux_panes_visible_after_exit(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="%1\n"),
        ]

        backend = TmuxBackend(session_name="test-team")
        backend.spawn_member(name="worker-1", prompt="/model", cwd="/tmp/workspace")

        assert mock_run.call_args_list[1][0][0] == [
            "tmux",
            "set-option",
            "-w",
            "-t",
            "test-team",
            "remain-on-exit",
            "on",
        ]

    @patch("subprocess.run")
    def test_spawn_creates_fallback_session_when_missing(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0, stdout="%1\n"),
        ]

        backend = TmuxBackend(session_name="test-team")
        backend.spawn_member(
            name="worker-1",
            prompt="Fix bug",
            cwd="/tmp/workspace",
        )

        cmd = mock_run.call_args_list[1][0][0]
        assert cmd[:5] == ["tmux", "new-session", "-d", "-s", "test-team"]

    @patch("subprocess.run")
    def test_kill_member(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        backend = TmuxBackend(session_name="test-team")
        pane = TmuxPane(pane_id="%1", session_name="test-team", member_name="w1")
        backend.kill_member(pane)

        # Should call tmux kill-pane
        cmd = mock_run.call_args[0][0]
        assert "kill-pane" in str(cmd) or "kill" in str(cmd)

    @patch("subprocess.run")
    def test_list_members(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="%1:worker-1:12345\n%2:worker-2:12346\n",
        )

        backend = TmuxBackend(session_name="test-team")
        backend._panes = {
            "worker-1": TmuxPane("%1", "test-team", "worker-1", 12345),
            "worker-2": TmuxPane("%2", "test-team", "worker-2", 12346),
        }
        members = backend.list_members()
        assert len(members) == 2

    def test_cleanup_empty(self):
        backend = TmuxBackend(session_name="test-team")
        backend.cleanup()  # Should not crash with no panes

    @patch("subprocess.run")
    def test_send_keys(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)

        backend = TmuxBackend(session_name="test-team")
        pane = TmuxPane(pane_id="%1", session_name="test-team", member_name="w1")
        backend.send_keys(pane, "hello")

        cmd = mock_run.call_args[0][0]
        assert "send-keys" in str(cmd)
        assert mock_run.call_count == 2
