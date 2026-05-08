"""Cached changelog helpers for the local release-notes command."""

from __future__ import annotations

import re
from itertools import zip_longest
from pathlib import Path

import requests

from koder_agent.harness.paths import harness_home_dir

CHANGELOG_URL = "https://github.com/feiskyer/koder/blob/main/CHANGELOG.md"
RAW_CHANGELOG_URL = "https://raw.githubusercontent.com/feiskyer/koder/refs/heads/main/CHANGELOG.md"


def get_changelog_cache_path() -> Path:
    return harness_home_dir() / "cache" / "changelog.md"


def get_stored_changelog() -> str:
    cache_path = get_changelog_cache_path()
    try:
        return cache_path.read_text(encoding="utf-8")
    except Exception:
        return ""


def fetch_and_store_changelog(*, timeout_seconds: float = 0.5) -> str:
    try:
        response = requests.get(RAW_CHANGELOG_URL, timeout=timeout_seconds)
    except Exception:
        return ""
    if response.status_code != 200:
        return ""
    changelog = response.text or ""
    if not changelog.strip():
        return ""
    cache_path = get_changelog_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(changelog, encoding="utf-8")
    return changelog


def parse_changelog(content: str) -> dict[str, list[str]]:
    if not content:
        return {}
    release_notes: dict[str, list[str]] = {}
    for section in re.split(r"^##\s+", content, flags=re.MULTILINE)[1:]:
        lines = [line.rstrip() for line in section.strip().splitlines()]
        if not lines:
            continue
        version = (lines[0].split(" - ", 1)[0] or "").strip()
        if not version:
            continue
        notes = [
            line.strip()[2:].strip()
            for line in lines[1:]
            if line.strip().startswith("- ") and line.strip()[2:].strip()
        ]
        if notes:
            release_notes[version] = notes
    return release_notes


def _coerce_version(version: str | None) -> tuple[int, ...] | None:
    if not version:
        return None
    match = re.search(r"\d+(?:\.\d+)+", version)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def _gt_version(left: str, right: str) -> bool:
    left_key = _coerce_version(left)
    right_key = _coerce_version(right)
    if left_key is None or right_key is None:
        return False
    for left_part, right_part in zip_longest(left_key, right_key, fillvalue=0):
        if left_part != right_part:
            return left_part > right_part
    return False


def _lte_version(left: str, right: str) -> bool:
    left_key = _coerce_version(left)
    right_key = _coerce_version(right)
    if left_key is None or right_key is None:
        return False
    for left_part, right_part in zip_longest(left_key, right_key, fillvalue=0):
        if left_part != right_part:
            return left_part < right_part
    return True


def get_recent_release_note_groups(
    current_version: str,
    previous_version: str | None,
    changelog_content: str,
    *,
    max_versions: int = 3,
) -> list[tuple[str, list[str]]]:
    release_notes = parse_changelog(changelog_content)
    if not release_notes:
        return []
    if _coerce_version(current_version) is None:
        return []
    if previous_version and not _gt_version(current_version, previous_version):
        return []

    groups = [
        (version, notes)
        for version, notes in release_notes.items()
        if notes
        and _lte_version(version, current_version)
        and (previous_version is None or _gt_version(version, previous_version))
    ]
    groups.sort(key=lambda item: _coerce_version(item[0]) or tuple(), reverse=True)
    return groups[:max_versions]


def get_all_release_notes(changelog_content: str) -> list[tuple[str, list[str]]]:
    groups = [
        (version, notes) for version, notes in parse_changelog(changelog_content).items() if notes
    ]
    groups.sort(key=lambda item: _coerce_version(item[0]) or tuple())
    return groups


def format_release_notes(groups: list[tuple[str, list[str]]]) -> str:
    return "\n\n".join(
        "\n".join([f"Version {version}:"] + [f"· {note}" for note in notes])
        for version, notes in groups
    )
