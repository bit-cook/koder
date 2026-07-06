from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "docs" / "features.md"
COMMANDS = ROOT / "docs" / "commands.md"
README = ROOT / "README.md"

TOPIC_GUIDES = [
    "docs/getting-started.md",
    "docs/interactive-tui.md",
    "docs/configuration.md",
    "docs/sessions-and-memory.md",
    "docs/agents-and-teams.md",
    "docs/workflows.md",
    "docs/extensions.md",
    "docs/permissions-and-privacy.md",
    "docs/voice-mode.md",
    "docs/commands.md",
]


def _read_user_docs() -> str:
    paths = [README, FEATURES, *(ROOT / path for path in TOPIC_GUIDES)]
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_readme_links_main_user_docs():
    text = README.read_text(encoding="utf-8")

    for relative_path in TOPIC_GUIDES:
        link = relative_path.removeprefix("docs/")
        if relative_path.startswith("docs/"):
            assert f"({relative_path})" in text or f"({link})" in text


def test_feature_guide_links_topic_docs():
    text = FEATURES.read_text(encoding="utf-8")

    for relative_path in TOPIC_GUIDES:
        link = relative_path.removeprefix("docs/")
        assert f"]({link})" in text


def test_user_docs_do_not_link_internal_tmux_validation_design():
    text = _read_user_docs()
    internal_doc_slug = "tmux" + "-validation-design"
    internal_doc_title = "Tmux Feature" + " Validation Design"

    assert internal_doc_slug not in text
    assert internal_doc_title not in text
    assert "/".join(("docs", "audit")) not in text


def test_command_reference_has_no_generic_execute_descriptions():
    text = COMMANDS.read_text(encoding="utf-8")

    assert "| Execute /" not in text


def test_command_reference_has_no_test_fixture_wording():
    """Fixture vocabulary belongs in the scenario manifest, not user docs."""
    import re

    text = COMMANDS.read_text(encoding="utf-8")
    fixture_terms = re.compile(r"\b(stub|stubbed|fake|fixture)\b", re.IGNORECASE)

    for line in text.splitlines():
        if line.startswith("|"):
            assert not fixture_terms.search(line), line
