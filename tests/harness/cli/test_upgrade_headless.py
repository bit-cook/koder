from __future__ import annotations

import argparse
import subprocess

import pytest

from koder_agent.harness import upgrade
from koder_agent.harness.cli.headless import handle_upgrade_command


def test_detect_upgrade_plan_uv_tool(monkeypatch):
    monkeypatch.setattr(upgrade.sys, "executable", "/home/u/.local/share/uv/tools/koder/bin/python")
    monkeypatch.setattr(upgrade.sys, "argv", ["/home/u/.local/share/uv/tools/koder/bin/koder"])
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: f"/usr/bin/{name}")
    plan = upgrade.detect_upgrade_plan()
    assert plan.channel == "uv-tool"
    assert plan.command == ["uv", "tool", "upgrade", "koder"]


def test_detect_upgrade_plan_pipx(monkeypatch):
    monkeypatch.setattr(upgrade.sys, "executable", "/home/u/.local/pipx/venvs/koder/bin/python")
    monkeypatch.setattr(upgrade.sys, "argv", ["/home/u/.local/bin/koder"])

    def which(name):
        return f"/usr/bin/{name}" if name in {"pipx"} else None

    monkeypatch.setattr(upgrade.shutil, "which", which)
    plan = upgrade.detect_upgrade_plan()
    assert plan.channel == "pipx"
    assert plan.command == ["pipx", "upgrade", "koder"]


def test_detect_upgrade_plan_pip_fallback(monkeypatch):
    monkeypatch.setattr(upgrade.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(upgrade.sys, "argv", ["/usr/bin/koder"])
    monkeypatch.setattr(upgrade.shutil, "which", lambda name: None)
    plan = upgrade.detect_upgrade_plan()
    assert plan.channel == "pip"
    assert plan.command == ["/usr/bin/python3", "-m", "pip", "install", "--upgrade", "koder"]


def test_run_upgrade_success(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(upgrade.subprocess, "run", fake_run)
    plan = upgrade.UpgradePlan(channel="pip", command=["pip", "install", "-U", "koder"])
    code, message = upgrade.run_upgrade(plan)
    assert code == 0
    assert "Upgrade complete" in message
    assert calls == [["pip", "install", "-U", "koder"]]


def test_run_upgrade_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(upgrade.subprocess, "run", fake_run)
    plan = upgrade.UpgradePlan(channel="pip", command=["pip", "install", "-U", "koder"])
    code, message = upgrade.run_upgrade(plan)
    assert code == 3
    assert "Upgrade failed" in message


@pytest.mark.asyncio
async def test_handle_upgrade_dry_run(monkeypatch, capsys):
    monkeypatch.setattr(
        "koder_agent.harness.cli.headless.detect_upgrade_plan",
        lambda: upgrade.UpgradePlan(channel="uv-tool", command=["uv", "tool", "upgrade", "koder"]),
    )
    called = {"ran": False}

    def fake_run(plan):
        called["ran"] = True
        return (0, "should not run")

    monkeypatch.setattr("koder_agent.harness.cli.headless.run_upgrade", fake_run)
    exit_code = await handle_upgrade_command(argparse.Namespace(dry_run=True))
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "channel: uv-tool" in out
    assert "uv tool upgrade koder" in out
    assert called["ran"] is False


@pytest.mark.asyncio
async def test_handle_upgrade_runs(monkeypatch, capsys):
    monkeypatch.setattr(
        "koder_agent.harness.cli.headless.detect_upgrade_plan",
        lambda: upgrade.UpgradePlan(channel="pip", command=["pip", "install", "-U", "koder"]),
    )

    def fake_run(plan):
        return (0, "Upgrade complete via pip")

    monkeypatch.setattr("koder_agent.harness.cli.headless.run_upgrade", fake_run)
    exit_code = await handle_upgrade_command(argparse.Namespace(dry_run=False))
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Upgrade complete via pip" in out
