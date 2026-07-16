from __future__ import annotations

import argparse

import pytest

from koder_agent.harness.cli.completion import (
    SUPPORTED_SHELLS,
    render_completion_script,
)
from koder_agent.harness.cli.headless import handle_completion_command


def test_render_completion_supports_all_shells():
    for shell in SUPPORTED_SHELLS:
        script = render_completion_script(shell)
        assert "koder" in script
        assert "approve" in script
        assert script.strip()


def test_render_completion_bash_has_complete_directive():
    script = render_completion_script("bash")
    assert "complete -F _koder_completion koder" in script
    assert "doctor" in script
    assert "review" in script


def test_render_completion_zsh_has_compdef():
    script = render_completion_script("zsh")
    assert "#compdef koder" in script
    assert "compdef _koder koder" in script


def test_render_completion_fish_uses_subcommand_guard():
    script = render_completion_script("fish")
    assert "__fish_use_subcommand" in script
    assert "completion" in script


def test_render_completion_rejects_unknown_shell():
    with pytest.raises(ValueError):
        render_completion_script("powershell")


def test_handle_completion_command_prints_script(capsys):
    args = argparse.Namespace(shell="bash")
    exit_code = handle_completion_command(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "_koder_completion" in out


def test_handle_completion_command_normalizes_case(capsys):
    args = argparse.Namespace(shell="ZSH")
    exit_code = handle_completion_command(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "#compdef koder" in out
