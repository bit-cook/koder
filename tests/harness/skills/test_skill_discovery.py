"""Tests for dynamic skill discovery."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from koder_agent.harness.skills.discovery import (
    activate_conditional_skills,
    discover_skills_for_paths,
)


class TestDiscoverSkillsForPaths:
    """Test skill directory discovery from file paths."""

    def test_discovers_koder_skills_in_parent_directories(self):
        """Should find .koder/skills/ directories in parent paths."""
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()

            # Create directory structure
            project_skills = base / "project" / ".koder" / "skills"
            project_skills.mkdir(parents=True)

            nested_skills = base / "project" / "src" / "lib" / ".koder" / "skills"
            nested_skills.mkdir(parents=True)

            # File paths that should trigger discovery
            file_paths = [
                str(base / "project" / "src" / "lib" / "module.py"),
                str(base / "project" / "README.md"),
            ]

            discovered = discover_skills_for_paths(file_paths, set())

            # Should find both skill directories
            assert len(discovered) == 2
            assert project_skills.resolve() in discovered
            assert nested_skills.resolve() in discovered

    def test_skips_already_known_directories(self):
        """Should not return directories already in known_dirs."""
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            skills_dir = base / ".koder" / "skills"
            skills_dir.mkdir(parents=True)

            file_paths = [str(base / "file.py")]
            known_dirs = {str(skills_dir)}

            discovered = discover_skills_for_paths(file_paths, known_dirs)

            assert len(discovered) == 0

    def test_returns_empty_for_no_skill_directories(self):
        """Should return empty list when no .koder/skills/ found."""
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            file_paths = [str(base / "file.py")]

            discovered = discover_skills_for_paths(file_paths, set())

            assert discovered == []

    def test_handles_empty_paths_list(self):
        """Should return empty list for empty input."""
        discovered = discover_skills_for_paths([], set())
        assert discovered == []

    def test_deduplicates_skill_directories(self):
        """Should return each skill directory only once."""
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()

            skills_dir = base / ".koder" / "skills"
            skills_dir.mkdir(parents=True)

            # Multiple files in same project
            file_paths = [
                str(base / "file1.py"),
                str(base / "file2.py"),
                str(base / "src" / "file3.py"),
            ]

            discovered = discover_skills_for_paths(file_paths, set())

            assert len(discovered) == 1
            assert skills_dir.resolve() in discovered


class TestActivateConditionalSkills:
    """Test conditional skill activation based on file paths."""

    def test_activates_skills_with_matching_path_patterns(self):
        """Should return skill names when path patterns match."""
        skills = {
            "python-dev": Mock(
                name="python-dev",
                metadata={"paths": ["**/*.py", "**/*.pyi"]},
            ),
            "js-dev": Mock(
                name="js-dev",
                metadata={"paths": ["**/*.js", "**/*.ts"]},
            ),
        }

        activated = activate_conditional_skills(skills, "src/module.py")

        assert activated == ["python-dev"]

    def test_non_matching_paths_dont_activate(self):
        """Should not activate skills when patterns don't match."""
        skills = {
            "python-dev": Mock(
                name="python-dev",
                metadata={"paths": ["**/*.py"]},
            ),
        }

        activated = activate_conditional_skills(skills, "README.md")

        assert activated == []

    def test_returns_empty_for_skills_without_paths(self):
        """Should ignore skills without paths field."""
        skills = {
            "always-on": Mock(
                name="always-on",
                metadata={},  # No paths field
            ),
        }

        activated = activate_conditional_skills(skills, "any/file.py")

        assert activated == []

    def test_handles_empty_paths_list_in_metadata(self):
        """Should handle skills with empty paths list."""
        skills = {
            "no-paths": Mock(
                name="no-paths",
                metadata={"paths": []},
            ),
        }

        activated = activate_conditional_skills(skills, "file.py")

        assert activated == []

    def test_multiple_skills_can_activate(self):
        """Should activate all skills with matching patterns."""
        skills = {
            "python-dev": Mock(
                name="python-dev",
                metadata={"paths": ["**/*.py"]},
            ),
            "test-helper": Mock(
                name="test-helper",
                metadata={"paths": ["**/test_*.py", "**/*_test.py"]},
            ),
        }

        activated = activate_conditional_skills(skills, "tests/test_module.py")

        assert len(activated) == 2
        assert "python-dev" in activated
        assert "test-helper" in activated

    def test_handles_complex_patterns(self):
        """Should handle fnmatch patterns correctly."""
        skills = {
            "config": Mock(
                name="config",
                metadata={"paths": ["**/config/*.yaml", "**/config/*.yml"]},
            ),
        }

        activated = activate_conditional_skills(skills, "project/config/settings.yaml")
        assert activated == ["config"]

        activated = activate_conditional_skills(skills, "project/config/settings.json")
        assert activated == []

    def test_handles_absolute_paths(self):
        """Should work with absolute file paths."""
        skills = {
            "python-dev": Mock(
                name="python-dev",
                metadata={"paths": ["**/*.py"]},
            ),
        }

        activated = activate_conditional_skills(skills, "/absolute/path/to/file.py")

        assert activated == ["python-dev"]


class TestActivateConditionalSkillsPathsField:
    """Test activation reads the dedicated Skill.paths field, not just metadata."""

    def test_real_skill_paths_field_activates(self):
        """A real Skill object stores patterns in .paths (stripped from metadata)."""
        from koder_agent.tools.skill import Skill

        skill = Skill(
            name="python-dev",
            description="Python helper",
            content="body",
            metadata=None,  # paths is NOT in metadata on real skills
            paths=["**/*.py"],
        )

        activated = activate_conditional_skills({"python-dev": skill}, "src/module.py")
        assert activated == ["python-dev"]

    def test_real_skill_paths_field_no_match(self):
        """Real Skill with paths that do not match returns no activation."""
        from koder_agent.tools.skill import Skill

        skill = Skill(
            name="python-dev",
            description="Python helper",
            content="body",
            metadata=None,
            paths=["**/*.py"],
        )

        activated = activate_conditional_skills({"python-dev": skill}, "README.md")
        assert activated == []

    def test_real_skill_without_paths_not_activated(self):
        """Non-regression: a real Skill with paths=None never activates."""
        from koder_agent.tools.skill import Skill

        skill = Skill(
            name="always-on",
            description="No conditional paths",
            content="body",
            metadata={"other": "value"},
            paths=None,
        )

        activated = activate_conditional_skills({"always-on": skill}, "src/module.py")
        assert activated == []

    def test_metadata_paths_still_supported_for_mock_inputs(self):
        """Non-regression: Mock inputs using metadata['paths'] still activate.

        Mock auto-creates a truthy ``.paths`` attribute, so activation must fall
        back to ``metadata['paths']`` unless ``.paths`` is a real list/tuple.
        """
        skill_obj = Mock(name="cfg", metadata={"paths": ["**/*.yaml"]})
        activated = activate_conditional_skills({"cfg": skill_obj}, "conf/app.yaml")
        assert activated == ["cfg"]
