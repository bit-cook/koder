from __future__ import annotations

import pytest

from koder_agent.cli import _build_cli_parser
from koder_agent.harness.cli.entrypoint import build_runtime_request


@pytest.mark.parametrize(
    "argv",
    [
        ["doctor"],
        ["doctor", "--json"],
        ["review"],
        ["review", "--base", "main"],
        ["review", "--uncommitted"],
        ["review", "#123"],
        ["completion", "bash"],
        ["upgrade"],
        ["upgrade", "--dry-run"],
    ],
)
def test_new_subcommands_classified(argv):
    request = build_runtime_request(argv)
    assert request.mode == "subcommand"
    assert request.first_arg == argv[0]


def test_config_validate_and_show_effective_classified():
    assert build_runtime_request(["config", "validate"]).mode == "subcommand"
    assert build_runtime_request(["config", "show", "--effective"]).mode == "subcommand"


def test_parser_parses_doctor_json():
    parser = _build_cli_parser("doctor")
    args = parser.parse_args(["doctor", "--json"])
    assert args.command == "doctor"
    assert args.json_output is True


def test_parser_parses_review_flags():
    parser = _build_cli_parser("review")
    args = parser.parse_args(["review", "--base", "develop"])
    assert args.command == "review"
    assert args.base == "develop"
    assert args.uncommitted is False

    args2 = parser.parse_args(["review", "#77", "--uncommitted"])
    assert args2.target == "#77"
    assert args2.uncommitted is True


def test_parser_parses_completion_shell():
    parser = _build_cli_parser("completion")
    args = parser.parse_args(["completion", "fish"])
    assert args.command == "completion"
    assert args.shell == "fish"


def test_parser_parses_config_show_effective():
    parser = _build_cli_parser("config")
    args = parser.parse_args(["config", "show", "--effective"])
    assert args.command == "config"
    assert args.config_action == "show"
    assert args.effective is True


def test_parser_parses_config_validate():
    parser = _build_cli_parser("config")
    args = parser.parse_args(["config", "validate"])
    assert args.config_action == "validate"


def test_parser_parses_auth_status_json():
    parser = _build_cli_parser("auth")
    args = parser.parse_args(["auth", "status", "--json"])
    assert args.command == "auth"
    assert args.auth_command == "status"
    assert args.json_output is True


def test_parser_parses_auth_login_token():
    parser = _build_cli_parser("auth")
    args = parser.parse_args(["auth", "login", "claude", "--token", "-"])
    assert args.auth_command == "login"
    assert args.token == "-"


def test_parser_parses_resume_all_flag():
    parser = _build_cli_parser(None)
    args = parser.parse_args(["--resume", "my-title", "--all"])
    assert args.resume == "my-title"
    assert args.resume_all is True


def test_help_lists_new_commands():
    from koder_agent.cli import _append_subcommand_help

    parser = _build_cli_parser(None)
    help_text = _append_subcommand_help(parser.format_help())
    for command in ("doctor", "review", "completion", "upgrade"):
        assert command in help_text
