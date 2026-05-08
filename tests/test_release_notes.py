from __future__ import annotations

from koder_agent.harness.release_notes import (
    format_release_notes,
    get_recent_release_note_groups,
    parse_changelog,
)


def test_parse_changelog_extracts_version_groups():
    parsed = parse_changelog(
        "\n".join(
            [
                "# Changelog",
                "",
                "## 0.4.13 - 2026-04-09",
                "- Added configurable statusline setup",
                "- Improved command output formatting",
                "",
                "## 0.4.12 - 2026-04-01",
                "- Added performance improvements",
            ]
        )
    )

    assert parsed["0.4.13"] == [
        "Added configurable statusline setup",
        "Improved command output formatting",
    ]
    assert parsed["0.4.12"] == ["Added performance improvements"]


def test_get_recent_release_note_groups_filters_by_last_seen_version():
    changelog = "\n".join(
        [
            "# Changelog",
            "",
            "## 0.4.13 - 2026-04-09",
            "- Added configurable statusline setup",
            "",
            "## 0.4.12 - 2026-04-01",
            "- Added performance improvements",
            "",
            "## 0.4.11 - 2026-03-28",
            "- Older release note",
        ]
    )

    groups = get_recent_release_note_groups("0.4.13", "0.4.12", changelog, max_versions=3)

    assert groups == [("0.4.13", ["Added configurable statusline setup"])]
    assert format_release_notes(groups) == (
        "Version 0.4.13:\n· Added configurable statusline setup"
    )
