"""Tests for skill-loader polish items.

Covers three fixes in ``koder_agent/tools/skill.py``:

1. ``discover_merged_skills`` warns when a same-named skill from a *different*
   source shadows an existing one (previously silent last-writer-wins).
2. ``_get_merged_skills`` cache key folds mtimes over the FULL skill-dir set
   (walked-up / nested / dynamically discovered), so editing a nested
   ``.koder/skills`` SKILL.md yields a fresh cache key without a restart.
3. Metadata budgeting is token-accurate (via ``estimate_text_tokens``) instead
   of a fixed 4-chars-per-token heuristic, respecting a small token cap.
"""

import logging
import sys
import textwrap
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.memory.budget import estimate_text_tokens  # noqa: E402
from koder_agent.tools import skill as skill_module  # noqa: E402
from koder_agent.tools.skill import (  # noqa: E402
    _compute_merged_skills_cache_key,
    build_skills_metadata_prompt,
    discover_merged_skills,
)


@pytest.fixture(autouse=True)
def reset_skill_cache():
    skill_module._merged_skills = None
    skill_module._merged_skills_key = None
    yield
    skill_module._merged_skills = None
    skill_module._merged_skills_key = None


def _write_skill(path: Path, *, name: str, description: str = "desc", body: str = "content"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        textwrap.dedent(f"""\
            ---
            name: {name}
            description: {description}
            ---
            {body}
            """),
        encoding="utf-8",
    )


class _SkillStub:
    """Minimal stand-in for ``Skill`` used in metadata-budget tests."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.disable_model_invocation = False
        self.paths = None


# ---------------------------------------------------------------------------
# Item 1: cross-source shadow warning
# ---------------------------------------------------------------------------


def test_cross_source_shadow_emits_warning(tmp_path, monkeypatch, caplog):
    """A user skill sharing a bundled skill's name logs a shadow warning."""
    user_home = tmp_path / "home"
    project_root = tmp_path / "project"
    user_home.mkdir()
    project_root.mkdir()
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.chdir(project_root)

    # Pick a real bundled skill name so the user copy shadows a "bundled" source.
    bundled = discover_merged_skills(cwd=project_root, user_dir=user_home / ".koder/skills")
    bundled_names = [n for n, s in bundled.items() if s.source == "bundled"]
    assert bundled_names, "expected at least one bundled skill to shadow"
    target = bundled_names[0]

    _write_skill(
        user_home / ".koder/skills" / target / "SKILL.md",
        name=target,
        description="user override copy",
    )

    with caplog.at_level(logging.WARNING, logger="koder_agent.tools.skill"):
        merged = discover_merged_skills(cwd=project_root, user_dir=user_home / ".koder/skills")

    # Precedence unchanged: the user copy wins (last writer).
    assert merged[target].source == "user"
    # ...but it is no longer silent.
    assert f"skill '{target}'" in caplog.text
    assert "source 'user'" in caplog.text
    assert "source 'bundled'" in caplog.text


def test_same_source_override_does_not_warn(tmp_path, monkeypatch, caplog):
    """Two project dirs contributing the same name stay silent (same source)."""
    user_home = tmp_path / "home"
    project_root = tmp_path / "project"
    nested_pkg = project_root / "packages" / "web"
    user_home.mkdir()
    nested_pkg.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.chdir(project_root)

    # Root project skill and a nested-package project skill share a name; both
    # carry source="project", so no cross-source warning should fire.
    _write_skill(
        project_root / ".koder/skills" / "dup" / "SKILL.md",
        name="dup",
        description="root project copy",
    )
    _write_skill(
        nested_pkg / ".koder/skills" / "dup" / "SKILL.md",
        name="dup",
        description="nested project copy",
    )

    with caplog.at_level(logging.WARNING, logger="koder_agent.tools.skill"):
        merged = discover_merged_skills(cwd=project_root, user_dir=user_home / ".koder/skills")

    assert "dup" in merged
    assert merged["dup"].source == "project"
    # No cross-source override warning for two same-source project dirs.
    assert "overrides skill of the same name" not in caplog.text


# ---------------------------------------------------------------------------
# Item 2: cache key reflects nested .koder/skills edits
# ---------------------------------------------------------------------------


def _key_for(tmp_path: Path, cwd: Path) -> tuple:
    return _compute_merged_skills_cache_key(
        cwd=str(cwd.resolve()),
        user_dir=str((tmp_path / "home" / ".koder" / "skills").resolve()),
        project_dir=str((cwd / ".koder" / "skills").resolve()),
        plugin_root=str((tmp_path / "home" / ".koder" / "plugins").resolve()),
        additional=(),
    )


def test_nested_skill_edit_changes_cache_key(tmp_path, monkeypatch):
    """Editing a nested .koder/skills SKILL.md changes the cache key."""
    user_home = tmp_path / "home"
    project_root = tmp_path / "project"
    nested = project_root / "packages" / "frontend" / ".koder" / "skills" / "frontend-skill"
    user_home.mkdir()
    nested.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.chdir(project_root)

    nested_file = nested / "SKILL.md"
    _write_skill(nested_file, name="frontend-skill", description="v1", body="original")

    key_before = _key_for(tmp_path, project_root)

    # Bump the mtime far enough that filesystem granularity cannot mask it, then
    # rewrite the nested skill body.
    import os
    import time

    future = time.time() + 100
    _write_skill(nested_file, name="frontend-skill", description="v2", body="edited")
    os.utime(nested_file, (future, future))

    key_after = _key_for(tmp_path, project_root)

    assert key_before != key_after, "nested SKILL.md edit must invalidate the cache key"


def test_cache_key_stable_without_edits(tmp_path, monkeypatch):
    """Non-regression: identical filesystem state yields an identical key."""
    user_home = tmp_path / "home"
    project_root = tmp_path / "project"
    nested = project_root / "packages" / "frontend" / ".koder" / "skills" / "frontend-skill"
    user_home.mkdir()
    nested.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.chdir(project_root)

    _write_skill(nested / "SKILL.md", name="frontend-skill", description="v1")

    key1 = _key_for(tmp_path, project_root)
    key2 = _key_for(tmp_path, project_root)
    assert key1 == key2


def test_get_merged_skills_refreshes_after_nested_edit(tmp_path, monkeypatch):
    """End-to-end: _get_merged_skills serves fresh content after a nested edit."""
    import os
    import time

    from koder_agent.config.models import KoderConfig, SkillsConfig

    user_home = tmp_path / "home"
    project_root = tmp_path / "project"
    nested = project_root / "packages" / "api" / ".koder" / "skills" / "nested-skill"
    user_home.mkdir()
    nested.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: user_home)
    monkeypatch.chdir(project_root)

    cfg = KoderConfig(
        skills=SkillsConfig(
            enabled=True,
            user_skills_dir=str(user_home / ".koder" / "skills"),
            project_skills_dir=str(project_root / ".koder" / "skills"),
        )
    )
    monkeypatch.setattr(skill_module, "get_config", lambda: cfg)
    monkeypatch.setattr(skill_module, "harness_home_dir", lambda: user_home / ".koder")

    nested_file = nested / "SKILL.md"
    _write_skill(nested_file, name="nested-skill", description="d", body="first body")

    skills1 = skill_module._get_merged_skills()
    assert "nested-skill" in skills1
    assert "first body" in skills1["nested-skill"].content

    future = time.time() + 100
    _write_skill(nested_file, name="nested-skill", description="d", body="second body")
    os.utime(nested_file, (future, future))

    skills2 = skill_module._get_merged_skills()
    assert "second body" in skills2["nested-skill"].content
    assert "first body" not in skills2["nested-skill"].content


# ---------------------------------------------------------------------------
# Item 3: token-accurate metadata budgeting
# ---------------------------------------------------------------------------


def test_metadata_budget_respects_small_token_cap(monkeypatch):
    """A small token cap trims the listing on token boundaries, not char count."""
    monkeypatch.delenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", raising=False)
    skills = {
        "alpha": _SkillStub("alpha", "alpha " * 40),
        "beta": _SkillStub("beta", "beta " * 40),
        "gamma": _SkillStub("gamma", "gamma " * 40),
    }
    # 30 tokens is enough for the header + roughly one entry, not all three.
    monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "30")

    prompt = build_skills_metadata_prompt(skills)

    # The whole prompt must respect the token cap.
    assert estimate_text_tokens(prompt) <= 30
    # At least the first skill (sorted) is present; a later one is dropped.
    assert "alpha" in prompt
    assert "gamma" not in prompt


def test_metadata_budget_is_token_accurate_not_char_heuristic(monkeypatch):
    """With no env override, the budget scales by tokens of the context window.

    A context window that yields a ~10-token budget must trim aggressively;
    the resulting prompt's *token* count (not char count) obeys the budget.
    """
    monkeypatch.delenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", raising=False)
    skills = {
        "one": _SkillStub("one", "word " * 60),
        "two": _SkillStub("two", "word " * 60),
    }

    # 1% of 1000 tokens == 10 tokens.
    prompt = build_skills_metadata_prompt(skills, context_window_tokens=1000)

    assert estimate_text_tokens(prompt) <= 10
    assert prompt.startswith("Available skills:")


def test_metadata_budget_full_listing_when_budget_ample(monkeypatch):
    """Non-regression: an ample budget lists every skill in full."""
    monkeypatch.delenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", raising=False)
    skills = {
        "alpha": _SkillStub("alpha", "short alpha description"),
        "beta": _SkillStub("beta", "short beta description"),
    }

    prompt = build_skills_metadata_prompt(skills)  # DEFAULT_TOKEN_BUDGET

    assert "- alpha: short alpha description" in prompt
    assert "- beta: short beta description" in prompt
    # Descriptions are not truncated (no ellipsis) when they fit comfortably.
    assert "…" not in prompt
