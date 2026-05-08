from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_upgrade_dependency_help_does_not_check_pypi():
    result = subprocess.run(
        [sys.executable, "scripts/upgrade_dependency.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "Usage: uv run scripts/upgrade_dependency.py [--help]" in result.stdout
    assert "Checking " not in result.stdout


def test_upgrade_dependency_rejects_unknown_arguments():
    result = subprocess.run(
        [sys.executable, "scripts/upgrade_dependency.py", "--unknown"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Usage: uv run scripts/upgrade_dependency.py [--help]" in result.stdout
