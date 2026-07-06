"""Tests for bundled skills completeness."""

from koder_agent.harness.hooks.runtime import HOOK_EVENTS
from koder_agent.harness.skills.bundled import (
    BUNDLED_SKILLS_DIR,
    _definitions,
    get_bundled_skills,
)


def test_bundled_skills_count():
    """Should have at least 9 bundled skills."""
    skills = get_bundled_skills()
    assert len(skills) >= 9


def test_required_skills_exist():
    """All required bundled skills should exist."""
    skills = get_bundled_skills()
    required = [
        "batch",
        "code-review",
        "debug",
        "init-explore",
        "loop",
        "simplify",
        "remember",
        "review-spec",
        "run",
        "security-review",
        "stuck",
        "verify",
        "update-config",
        "fewer-permission-prompts",
    ]
    for name in required:
        assert name in skills, f"Missing bundled skill: {name}"


def test_remember_skill():
    skills = get_bundled_skills()
    s = skills["remember"]
    assert "memory" in s.description.lower() or "remember" in s.description.lower()
    assert s.content  # Has content


def test_stuck_skill():
    skills = get_bundled_skills()
    s = skills["stuck"]
    assert "stuck" in s.description.lower() or "debug" in s.description.lower()
    assert s.content


def test_verify_skill():
    skills = get_bundled_skills()
    s = skills["verify"]
    assert "verify" in s.description.lower() or "test" in s.description.lower()
    assert s.content


def test_update_config_skill():
    skills = get_bundled_skills()
    s = skills["update-config"]
    assert "config" in s.description.lower() or "setting" in s.description.lower()
    assert s.content


def test_update_config_skill_points_to_runtime_hook_events():
    skills = get_bundled_skills()
    content = skills["update-config"].content

    assert "HOOK_EVENTS" in content
    assert "Hook events include:" not in content
    for event_name in ["ConfigChange", "CwdChanged", "InstructionsLoaded", "FileChanged"]:
        assert event_name in HOOK_EVENTS
        assert event_name in content


def test_loop_skill_delegates_to_runtime_command():
    skills = get_bundled_skills()
    s = skills["loop"]

    assert s.disable_model_invocation is True
    assert "/loop" in s.content
    assert "@every:300" in s.content
    assert "45m" in s.content
    assert "execute the prompt once immediately" not in s.content


def test_user_only_skills_disable_model_invocation():
    """batch/debug/init-explore/loop are user-invoked only; the rest stay model-invocable."""
    skills = get_bundled_skills()
    for name in ["batch", "debug", "init-explore", "loop"]:
        assert skills[name].disable_model_invocation is True, name
    for name in [
        "code-review",
        "review-spec",
        "verify",
        "security-review",
        "simplify",
        "remember",
        "run",
        "stuck",
        "update-config",
        "fewer-permission-prompts",
    ]:
        assert skills[name].disable_model_invocation is False, name


def test_all_skills_have_descriptions():
    for defn in _definitions():
        assert defn.description, f"Skill '{defn.name}' missing description"
        assert len(defn.description) > 10, f"Skill '{defn.name}' description too short"


def test_all_skills_have_content():
    for defn in _definitions():
        assert defn.content, f"Skill '{defn.name}' missing content"


def test_bundled_skills_do_not_hardcode_koder_repo_gates():
    """Bundled skills are generic prompts, not Koder-repo-only runbooks."""
    forbidden = [
        "for this repo:",
        "uv run ruff check <files>",
        "uv run pytest <test files>",
    ]
    for defn in _definitions():
        for phrase in forbidden:
            assert phrase not in defn.content, f"{defn.name} hardcodes {phrase!r}"


def test_every_markdown_file_loads_with_valid_frontmatter():
    """Every .md in bundled_skills/ must parse into a definition (none skipped)."""
    md_files = sorted(BUNDLED_SKILLS_DIR.glob("*.md"))
    assert md_files, "No bundled skill markdown files found"

    definitions = {defn.name for defn in _definitions()}
    assert len(definitions) == len(md_files), (
        "Some bundled skill markdown files failed to load: "
        f"{len(md_files)} files but {len(definitions)} definitions"
    )
    for path in md_files:
        assert path.stem in definitions, f"Bundled skill file {path.name} did not load"


def test_malformed_skill_file_is_skipped_with_warning(tmp_path, monkeypatch, caplog):
    """A markdown file without name/description is skipped, not fatal."""
    import logging

    import koder_agent.harness.skills.bundled as bundled

    good = tmp_path / "good.md"
    good.write_text(
        "---\nname: good\ndescription: a perfectly valid bundled skill\n---\nBody.",
        encoding="utf-8",
    )
    (tmp_path / "no-frontmatter.md").write_text("Just a body.", encoding="utf-8")
    (tmp_path / "missing-name.md").write_text(
        "---\ndescription: has no name\n---\nBody.", encoding="utf-8"
    )

    monkeypatch.setattr(bundled, "BUNDLED_SKILLS_DIR", tmp_path)
    with caplog.at_level(logging.WARNING):
        definitions = bundled._definitions()

    assert [defn.name for defn in definitions] == ["good"]
    assert any("no-frontmatter.md" in record.message for record in caplog.records)
    assert any("missing-name.md" in record.message for record in caplog.records)


def test_key_skills_have_substantial_content():
    """The flagship skills should ship full prompts, not stubs."""
    skills = get_bundled_skills()
    for name in ["code-review", "verify", "security-review", "update-config", "simplify"]:
        assert len(skills[name].content) > 500, f"Skill '{name}' content is too short"
