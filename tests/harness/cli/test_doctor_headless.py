from __future__ import annotations

import argparse
import json

import pytest

from koder_agent.harness import diagnostics
from koder_agent.harness.cli.headless import handle_doctor_command

SAMPLE_REPORT = {
    "cwd": "/home/user/project",
    "python": "/home/user/.venv/bin/python",
    "installation_type": "development",
    "invoked_binary": "/home/user/.venv/bin/koder",
    "config_path": "/home/user/.koder/config.yaml",
    "model": "gpt-4.1",
    "provider": "openai",
    "permission_mode": "default",
    "mcp_servers": 2,
    "ripgrep_working": True,
    "ripgrep_mode": "system",
    "ripgrep_path": "/usr/bin/rg",
}


def test_render_doctor_text_includes_keys():
    text = diagnostics.render_doctor_text(SAMPLE_REPORT)
    assert "model: gpt-4.1" in text
    assert "mcp_servers: 2" in text
    assert "ripgrep_working: true" in text


def test_redact_doctor_report_collapses_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setattr(diagnostics.Path, "home", classmethod(lambda cls: home))
    report = dict(SAMPLE_REPORT)
    report["cwd"] = str(home / "project")
    report["config_path"] = str(home / ".koder" / "config.yaml")
    redacted = diagnostics.redact_doctor_report(report)
    assert redacted["cwd"] == "~/project"
    assert redacted["config_path"] == "~/.koder/config.yaml"
    # Non-home paths are left untouched.
    assert redacted["python"] == SAMPLE_REPORT["python"]


@pytest.mark.asyncio
async def test_handle_doctor_command_text(monkeypatch, capsys):
    async def fake_collect():
        return dict(SAMPLE_REPORT)

    monkeypatch.setattr("koder_agent.harness.cli.headless.collect_doctor_report", fake_collect)
    exit_code = await handle_doctor_command(argparse.Namespace(json_output=False))
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "model: gpt-4.1" in out
    assert out.lstrip()[0] != "{"


@pytest.mark.asyncio
async def test_handle_doctor_command_json(monkeypatch, capsys):
    async def fake_collect():
        return dict(SAMPLE_REPORT)

    monkeypatch.setattr("koder_agent.harness.cli.headless.collect_doctor_report", fake_collect)
    exit_code = await handle_doctor_command(argparse.Namespace(json_output=True))
    out = capsys.readouterr().out
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["model"] == "gpt-4.1"
    assert payload["mcp_servers"] == 2
