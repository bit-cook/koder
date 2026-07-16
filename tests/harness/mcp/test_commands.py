from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys

from koder_agent.config import reset_config_manager
from koder_agent.config.manager import ConfigManager
from koder_agent.harness.mcp.commands import handle_mcp_subcommand


def _approve_args(*, yes: bool) -> argparse.Namespace:
    return argparse.Namespace(mcp_action="approve", yes=yes, source=[])


def test_noninteractive_approve_fails_immediately_with_actionable_text(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO())

    result = asyncio.run(handle_mcp_subcommand(_approve_args(yes=False)))

    output = capsys.readouterr().out
    assert result == 2
    assert "stdin is not a TTY" in output
    assert "rerun with --yes" in output


def test_approve_displays_resolved_execution_details_and_redacts_secrets(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    project = tmp_path / "project"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    project.mkdir()
    bin_dir.mkdir()
    server_executable = bin_dir / "actual-mcp"
    helper_executable = bin_dir / "header-helper"
    for executable in (server_executable, helper_executable):
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    (project / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "stdio": {
                        "command": "${MCP_EXEC}",
                        "args": ["serve", "${API_TOKEN}"],
                    },
                    "remote": {
                        "type": "http",
                        "url": "https://example.test/mcp?token=${API_TOKEN}&mode=review",
                        "headersHelper": "header-helper --json",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("MCP_EXEC", str(server_executable))
    monkeypatch.setenv("API_TOKEN", "super-secret-value")
    monkeypatch.setattr(ConfigManager, "DEFAULT_CONFIG_PATH", home / ".koder" / "config.yaml")
    reset_config_manager()
    monkeypatch.chdir(project)

    try:
        result = asyncio.run(handle_mcp_subcommand(_approve_args(yes=True)))
    finally:
        reset_config_manager()

    output = capsys.readouterr().out
    assert result == 0
    assert f"Executable: {server_executable.resolve()}" in output
    assert f'Argv: ["{server_executable.resolve()}", "serve", "<redacted>"]' in output
    assert "URL: https://example.test/mcp?token=%3Credacted%3E&mode=review" in output
    assert f"headersHelper executable: {helper_executable.resolve()}" in output
    assert f'headersHelper argv: ["{helper_executable.resolve()}", "--json"]' in output
    assert f"Execution directory: {project.resolve()}" in output
    assert f"PATH: {bin_dir.resolve()}" in output
    assert "super-secret-value" not in output
