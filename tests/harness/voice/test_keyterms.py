"""Tests for voice keyterms vocabulary hints."""

import subprocess
from unittest.mock import MagicMock, patch

from koder_agent.harness.voice.keyterms import (
    DOMAIN_KEYTERMS,
    get_all_keyterms,
    get_project_keyterms,
)


class TestDomainKeyterms:
    """Test domain keyterms list."""

    def test_has_expected_technical_terms(self):
        """Domain keyterms should include common technical terms."""
        expected_terms = [
            "MCP",
            "grep",
            "regex",
            "TypeScript",
            "Python",
            "pytest",
            "ripgrep",
            "git",
            "kubectl",
            "Docker",
            "npm",
            "pip",
            "uv",
        ]
        for term in expected_terms:
            assert term in DOMAIN_KEYTERMS, f"Expected {term} in DOMAIN_KEYTERMS"

    def test_is_list_of_strings(self):
        """Domain keyterms should be a list of strings."""
        assert isinstance(DOMAIN_KEYTERMS, list)
        assert all(isinstance(term, str) for term in DOMAIN_KEYTERMS)

    def test_has_reasonable_size(self):
        """Domain keyterms should have a reasonable number of entries."""
        assert len(DOMAIN_KEYTERMS) > 10, "Should have more than 10 domain keyterms"


class TestGetProjectKeyterms:
    """Test project-specific keyterm extraction."""

    @patch("subprocess.run")
    def test_extracts_from_git_branch_name(self, mock_run):
        """Should extract terms from git branch name split by delimiters."""
        mock_run.return_value = MagicMock(stdout="feature/add-voice-support\n", returncode=0)

        terms = get_project_keyterms("/fake/path")

        assert "feature" in terms
        assert "add" in terms
        assert "voice" in terms
        assert "support" in terms

    @patch("subprocess.run")
    def test_handles_git_command_failure(self, mock_run):
        """Should handle git command failures gracefully."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        terms = get_project_keyterms("/fake/path")

        # Should still work, just without git branch terms
        assert isinstance(terms, list)

    @patch("subprocess.run")
    def test_extracts_from_file_names(self, mock_run, tmp_path):
        """Should extract terms from top-level file names."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        # Create test files
        (tmp_path / "my_test_module.py").touch()
        (tmp_path / "another-file.ts").touch()
        (tmp_path / "README.md").touch()

        terms = get_project_keyterms(str(tmp_path))

        # "my" is filtered (only 2 chars)
        assert "test" in terms
        assert "module" in terms
        assert "another" in terms
        assert "file" in terms
        assert "README" in terms
        # Short terms should be filtered
        assert "my" not in terms
        assert "py" not in terms
        assert "ts" not in terms
        assert "md" not in terms

    @patch("subprocess.run")
    def test_extracts_from_pyproject_toml(self, mock_run, tmp_path):
        """Should extract package name from pyproject.toml."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        pyproject_content = """
[project]
name = "koder-agent"
version = "0.1.0"
"""
        (tmp_path / "pyproject.toml").write_text(pyproject_content)

        terms = get_project_keyterms(str(tmp_path))

        assert "koder" in terms
        assert "agent" in terms

    @patch("subprocess.run")
    def test_extracts_from_package_json(self, mock_run, tmp_path):
        """Should extract package name from package.json."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        package_json_content = """
{
  "name": "my-awesome-project",
  "version": "1.0.0"
}
"""
        (tmp_path / "package.json").write_text(package_json_content)

        terms = get_project_keyterms(str(tmp_path))

        # "my" is filtered (only 2 chars)
        assert "awesome" in terms
        assert "project" in terms
        assert "my" not in terms

    @patch("subprocess.run")
    def test_handles_missing_project_files(self, mock_run, tmp_path):
        """Should handle missing pyproject.toml and package.json gracefully."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        terms = get_project_keyterms(str(tmp_path))

        # Should still return a list, even if empty
        assert isinstance(terms, list)

    @patch("subprocess.run")
    def test_filters_short_terms(self, mock_run):
        """Should filter out very short terms (1-2 chars)."""
        mock_run.return_value = MagicMock(stdout="a/b/cd/real-term\n", returncode=0)

        terms = get_project_keyterms("/fake/path")

        # Short terms should be filtered
        assert "a" not in terms
        assert "b" not in terms
        assert "cd" not in terms
        assert "real" in terms
        assert "term" in terms


class TestGetAllKeyterms:
    """Test combined keyterm retrieval."""

    @patch("subprocess.run")
    def test_combines_domain_and_project_terms(self, mock_run, tmp_path):
        """Should combine domain and project keyterms."""
        mock_run.return_value = MagicMock(stdout="my-feature\n", returncode=0)
        (tmp_path / "custom_file.py").touch()

        all_terms = get_all_keyterms(str(tmp_path))

        # Should have domain terms
        assert "MCP" in all_terms
        assert "Python" in all_terms

        # Should have project terms (but "my" is filtered as it's only 2 chars)
        assert "feature" in all_terms
        assert "custom" in all_terms
        assert "file" in all_terms
        assert "my" not in all_terms

    @patch("subprocess.run")
    def test_deduplicates_terms(self, mock_run, tmp_path):
        """Should deduplicate terms appearing in both domain and project."""
        mock_run.return_value = MagicMock(stdout="python-project\n", returncode=0)

        all_terms = get_all_keyterms(str(tmp_path))

        # "Python" appears in DOMAIN_KEYTERMS, "python" from branch
        # Should only appear once (case-insensitive dedup)
        python_count = sum(1 for term in all_terms if term.lower() == "python")
        assert python_count == 1

    @patch("subprocess.run")
    def test_empty_project_returns_only_domain_terms(self, mock_run, tmp_path):
        """Should return only domain terms for empty project."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        all_terms = get_all_keyterms(str(tmp_path))

        # Should have domain terms
        assert "MCP" in all_terms
        assert "Python" in all_terms
        # Should be equal to DOMAIN_KEYTERMS (no duplicates, no project terms)
        assert len(all_terms) == len(DOMAIN_KEYTERMS)

    def test_returns_list_of_unique_strings(self, tmp_path):
        """Should return a list of unique strings."""
        all_terms = get_all_keyterms(str(tmp_path))

        assert isinstance(all_terms, list)
        assert all(isinstance(term, str) for term in all_terms)
        # Check uniqueness (case-sensitive)
        assert len(all_terms) == len(set(all_terms))
