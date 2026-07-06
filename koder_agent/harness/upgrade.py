"""Detect the install channel and run the appropriate upgrade command."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

PACKAGE_NAME = "koder"


@dataclass(frozen=True)
class UpgradePlan:
    """The detected upgrade channel and the command to run it."""

    channel: str
    command: list[str]


def detect_upgrade_plan() -> UpgradePlan:
    """Detect how koder was installed and return the matching upgrade command.

    Detection order:
    1. ``uv tool`` install (uv on PATH and koder under a uv tools dir)
    2. ``pipx`` install (pipx on PATH and koder under a pipx venvs dir)
    3. fallback to ``pip install --upgrade`` using the current interpreter
    """
    executable = sys.executable
    argv0 = sys.argv[0] if sys.argv and sys.argv[0] else executable
    location = f"{executable} {argv0}".lower()

    if shutil.which("uv") and ("uv/tools" in location or "uv\\tools" in location):
        return UpgradePlan(channel="uv-tool", command=["uv", "tool", "upgrade", PACKAGE_NAME])

    if shutil.which("pipx") and ("pipx" in location):
        return UpgradePlan(channel="pipx", command=["pipx", "upgrade", PACKAGE_NAME])

    return UpgradePlan(
        channel="pip",
        command=[sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME],
    )


def run_upgrade(plan: Optional[UpgradePlan] = None) -> tuple[int, str]:
    """Run the detected upgrade command.

    Returns ``(exit_code, message)``. The subprocess output is not captured so
    the user sees progress live; ``message`` summarizes the outcome.
    """
    plan = plan or detect_upgrade_plan()
    printable = " ".join(plan.command)
    try:
        result = subprocess.run(plan.command, check=False)
    except FileNotFoundError as exc:
        return 1, f"Upgrade command not found ({plan.channel}): {exc}"
    if result.returncode == 0:
        return 0, f"Upgrade complete via {plan.channel}: {printable}"
    return result.returncode, f"Upgrade failed via {plan.channel}: {printable}"
