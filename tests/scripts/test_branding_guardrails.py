from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCT_PATTERN = r"claude[\s_-]+" + "code"
FORBIDDEN_PATTERNS = [
    re.compile(PRODUCT_PATTERN, re.IGNORECASE),
    re.compile(r"migration\s+from\s+" + PRODUCT_PATTERN, re.IGNORECASE),
    re.compile(r"migrate\s+from\s+" + PRODUCT_PATTERN, re.IGNORECASE),
    re.compile(r"compatibility\s+from\s+" + PRODUCT_PATTERN, re.IGNORECASE),
    re.compile(PRODUCT_PATTERN + r"\s+compatibility", re.IGNORECASE),
    re.compile(PRODUCT_PATTERN + r"\s+migration", re.IGNORECASE),
]
SCAN_ROOTS = [
    ROOT / "README.md",
    ROOT / "AGENTS.md",
    ROOT / "docs",
    ROOT / "koder_agent",
    ROOT / "scripts",
    ROOT / "tests",
]


def _git_visible_files() -> set[Path] | None:
    """Files git would ship: tracked plus untracked-but-not-ignored."""
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        return None
    return {ROOT / line for line in result.stdout.splitlines() if line}


def _iter_scan_files() -> list[Path]:
    visible = _git_visible_files()
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            candidates = [root]
        else:
            candidates = [candidate for candidate in root.rglob("*") if candidate.is_file()]
        for candidate in candidates:
            if "__pycache__" in candidate.parts or candidate.suffix in {".pyc", ".png"}:
                continue
            if visible is not None and candidate not in visible:
                continue
            files.append(candidate)
    return sorted(files)


def test_human_facing_docs_and_source_avoid_reference_product_positioning():
    violations: list[str] = []
    for path in _iter_scan_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_PATTERNS:
            match = pattern.search(text)
            if match:
                rel = path.relative_to(ROOT)
                violations.append(f"{rel}: {match.group(0)!r}")
                break

    assert violations == []


def test_latest_commit_message_avoids_reference_product_positioning():
    result = subprocess.run(
        ["git", "log", "-1", "--format=%s%n%b"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0

    violations = [
        pattern.pattern for pattern in FORBIDDEN_PATTERNS if pattern.search(result.stdout)
    ]
    assert violations == []
