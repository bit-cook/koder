"""Helpers for git/workflow-oriented interactive commands."""

from __future__ import annotations

import subprocess


def _run_git(args: list[str], *, cwd: str | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    output = proc.stdout.strip() or proc.stderr.strip()
    return output


def current_branch(*, cwd: str | None = None) -> str:
    return _run_git(["branch", "--show-current"], cwd=cwd) or "No git branch detected."


def diff_stat(*, cwd: str | None = None) -> str:
    return _run_git(["diff", "--stat"], cwd=cwd) or "No working tree diff."


def staged_diff_stat(*, cwd: str | None = None) -> str:
    return _run_git(["diff", "--cached", "--stat"], cwd=cwd) or "No staged diff."


def status_short(*, cwd: str | None = None) -> str:
    return _run_git(["status", "--short"], cwd=cwd) or "Clean working tree."


def recent_commits(*, cwd: str | None = None, limit: int = 5) -> str:
    return _run_git(["log", f"-{limit}", "--oneline"], cwd=cwd) or "No git history available."


def remote_url(*, cwd: str | None = None) -> str:
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    if proc.returncode != 0:
        return "No git remote configured."
    return proc.stdout.strip() or "No git remote configured."
