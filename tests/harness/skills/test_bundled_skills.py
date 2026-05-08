"""Tests for bundled skills completeness."""

from koder_agent.harness.skills.bundled import _definitions, get_bundled_skills


def test_bundled_skills_count():
    """Should have at least 9 bundled skills."""
    skills = get_bundled_skills()
    assert len(skills) >= 9


def test_required_skills_exist():
    """All required bundled skills should exist."""
    skills = get_bundled_skills()
    required = [
        "batch",
        "claude-api",
        "debug",
        "loop",
        "simplify",
        "remember",
        "stuck",
        "verify",
        "update-config",
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


def test_all_skills_have_descriptions():
    for defn in _definitions():
        assert defn.description, f"Skill '{defn.name}' missing description"
        assert len(defn.description) > 10, f"Skill '{defn.name}' description too short"


def test_all_skills_have_content():
    for defn in _definitions():
        assert defn.content, f"Skill '{defn.name}' missing content"
