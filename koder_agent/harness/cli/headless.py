"""Headless handlers for top-level CLI subcommands.

These handlers print directly to stdout and return an exit code so they can be
driven by the harness session flow dispatch without any interactive UI.
"""

from __future__ import annotations

import argparse
import json

from koder_agent.harness.cli.completion import render_completion_script
from koder_agent.harness.diagnostics import (
    collect_doctor_report,
    redact_doctor_report,
    render_doctor_text,
)
from koder_agent.harness.review_flow import run_review
from koder_agent.harness.upgrade import detect_upgrade_plan, run_upgrade


async def handle_doctor_command(args: argparse.Namespace) -> int:
    """Handle `koder doctor [--json]`."""
    report = await collect_doctor_report()
    if getattr(args, "json_output", False):
        print(json.dumps(redact_doctor_report(report), ensure_ascii=False, indent=2))
        return 0
    print(render_doctor_text(report))
    return 0


async def handle_review_command(args: argparse.Namespace) -> int:
    """Handle `koder review [--base <ref>] [--uncommitted] [#PR]`.

    Returns a non-zero exit code when the review produced findings so it is
    usable as a CI gate.
    """
    target = getattr(args, "target", None)
    pr = target if target and target.startswith("#") else None
    base = getattr(args, "base", None)
    uncommitted = bool(getattr(args, "uncommitted", False))

    text, has_findings = await run_review(pr=pr, base=base, uncommitted=uncommitted)
    print(text)
    return 1 if has_findings else 0


def handle_completion_command(args: argparse.Namespace) -> int:
    """Handle `koder completion <bash|zsh|fish>`."""
    try:
        script = render_completion_script(args.shell)
    except ValueError as exc:
        print(str(exc))
        return 1
    print(script)
    return 0


async def handle_upgrade_command(args: argparse.Namespace) -> int:
    """Handle `koder upgrade [--dry-run]`."""
    plan = detect_upgrade_plan()
    if getattr(args, "dry_run", False):
        print(f"channel: {plan.channel}")
        print(f"command: {' '.join(plan.command)}")
        return 0
    exit_code, message = run_upgrade(plan)
    print(message)
    return exit_code
