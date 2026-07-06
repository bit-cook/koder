from __future__ import annotations

import argparse
import subprocess

import pytest

from koder_agent.harness import review_flow
from koder_agent.harness.cli.headless import handle_review_command


def _fake_run_factory(mapping):
    """Return a fake subprocess.run that keys off the argv tuple."""

    def fake_run(cmd, *args, **kwargs):
        key = tuple(cmd)
        result = mapping.get(key)
        if result is None:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        returncode, stdout, stderr = result
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    return fake_run


def test_select_review_diff_base(monkeypatch):
    monkeypatch.setattr(
        review_flow.subprocess,
        "run",
        _fake_run_factory({("git", "diff", "main...HEAD"): (0, "diff-content", "")}),
    )
    selection = review_flow.select_review_diff(base="main")
    assert selection.error is None
    assert selection.diff == "diff-content"
    assert selection.context == "main...HEAD"


def test_select_review_diff_uncommitted(monkeypatch):
    monkeypatch.setattr(
        review_flow.subprocess,
        "run",
        _fake_run_factory({("git", "diff", "HEAD"): (0, "uncommitted-diff", "")}),
    )
    selection = review_flow.select_review_diff(uncommitted=True)
    assert selection.diff == "uncommitted-diff"
    assert selection.context == "uncommitted changes"


def test_select_review_diff_uncommitted_empty(monkeypatch):
    monkeypatch.setattr(
        review_flow.subprocess,
        "run",
        _fake_run_factory({("git", "diff", "HEAD"): (0, "", "")}),
    )
    selection = review_flow.select_review_diff(uncommitted=True)
    assert selection.diff is None
    assert "No uncommitted changes" in selection.error


def test_select_review_diff_pr(monkeypatch):
    monkeypatch.setattr(
        review_flow.subprocess,
        "run",
        _fake_run_factory({("gh", "pr", "diff", "42"): (0, "pr-diff", "")}),
    )
    selection = review_flow.select_review_diff(pr="#42")
    assert selection.diff == "pr-diff"
    assert selection.context == "PR #42"


def test_select_review_diff_pr_gh_missing(monkeypatch):
    def fake_run(cmd, *args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(review_flow.subprocess, "run", fake_run)
    selection = review_flow.select_review_diff(pr="#42")
    assert selection.diff is None
    assert "gh CLI not found" in selection.error


def test_build_review_prompt_truncates():
    diff = "x" * (review_flow.DIFF_TRUNCATE_LIMIT + 100)
    prompt = review_flow.build_review_prompt(diff, "changes")
    assert "diff truncated" in prompt


@pytest.mark.asyncio
async def test_run_review_returns_findings(monkeypatch):
    monkeypatch.setattr(
        review_flow.subprocess,
        "run",
        _fake_run_factory({("git", "diff", "HEAD"): (0, "some-diff", "")}),
    )

    async def fake_completion(messages, **kwargs):
        return "Finding: bug on line 3"

    monkeypatch.setattr("koder_agent.utils.client.llm_completion", fake_completion)
    text, has_findings = await review_flow.run_review(uncommitted=True)
    assert has_findings is True
    assert "Finding" in text


@pytest.mark.asyncio
async def test_run_review_no_changes_returns_no_findings(monkeypatch):
    monkeypatch.setattr(
        review_flow.subprocess,
        "run",
        _fake_run_factory(
            {
                ("git", "diff", "HEAD"): (0, "", ""),
                ("git", "diff", "--cached"): (0, "", ""),
            }
        ),
    )
    text, has_findings = await review_flow.run_review()
    assert has_findings is False
    assert "No changes to review" in text


@pytest.mark.asyncio
async def test_handle_review_command_nonzero_on_findings(monkeypatch, capsys):
    async def fake_run_review(*, pr, base, uncommitted):
        return "Code Review: found issues", True

    monkeypatch.setattr("koder_agent.harness.cli.headless.run_review", fake_run_review)
    args = argparse.Namespace(target=None, base="main", uncommitted=False)
    exit_code = await handle_review_command(args)
    out = capsys.readouterr().out
    assert exit_code == 1
    assert "found issues" in out


@pytest.mark.asyncio
async def test_handle_review_command_zero_on_error(monkeypatch, capsys):
    async def fake_run_review(*, pr, base, uncommitted):
        return "No changes to review.", False

    monkeypatch.setattr("koder_agent.harness.cli.headless.run_review", fake_run_review)
    args = argparse.Namespace(target=None, base=None, uncommitted=True)
    exit_code = await handle_review_command(args)
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "No changes" in out


@pytest.mark.asyncio
async def test_handle_review_command_passes_pr(monkeypatch):
    captured = {}

    async def fake_run_review(*, pr, base, uncommitted):
        captured["pr"] = pr
        return "ok", False

    monkeypatch.setattr("koder_agent.harness.cli.headless.run_review", fake_run_review)
    args = argparse.Namespace(target="#7", base=None, uncommitted=False)
    await handle_review_command(args)
    assert captured["pr"] == "#7"
