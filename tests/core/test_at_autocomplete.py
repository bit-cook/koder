"""Tests for @ autocomplete: file index, completer, and mention processing."""

from __future__ import annotations

import os
import subprocess

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from koder_agent.core.at_completer import AtMentionCompleter, _find_at_trigger
from koder_agent.core.at_mentions import extract_at_file_mentions, process_at_file_mentions
from koder_agent.core.file_index import ProjectFileIndex, _fuzzy_score

# ---------------------------------------------------------------------------
# _find_at_trigger
# ---------------------------------------------------------------------------


class TestFindAtTrigger:
    def test_at_start(self):
        assert _find_at_trigger("@hello") == 0

    def test_after_space(self):
        assert _find_at_trigger("fix @src/main") == 4

    def test_after_tab(self):
        assert _find_at_trigger("fix\t@src") == 4

    def test_after_newline(self):
        assert _find_at_trigger("fix\n@src") == 4

    def test_no_at(self):
        assert _find_at_trigger("hello world") is None

    def test_at_without_preceding_whitespace(self):
        assert _find_at_trigger("user@host") is None

    def test_bare_at(self):
        assert _find_at_trigger("@") == 0

    def test_at_with_space_after(self):
        # Space after @ breaks the token — no trigger found
        assert _find_at_trigger("@ ") is None

    def test_multiple_at_returns_last_valid(self):
        # "hello @a @b" — should find the last one at position 9
        assert _find_at_trigger("hello @a @b") == 9

    def test_empty_string(self):
        assert _find_at_trigger("") is None


# ---------------------------------------------------------------------------
# _fuzzy_score
# ---------------------------------------------------------------------------


class TestFuzzyScore:
    def test_exact_substring(self):
        score = _fuzzy_score("main", "src/main.py")
        assert score > 0

    def test_exact_substring_at_boundary(self):
        s1 = _fuzzy_score("main", "src/main.py")
        s2 = _fuzzy_score("main", "src/domain.py")
        assert s1 > s2 or s2 == -1

    def test_no_match(self):
        assert _fuzzy_score("xyz", "abc.py") == -1

    def test_fuzzy_match(self):
        score = _fuzzy_score("mp", "src/main.py")
        assert score > 0  # m and p both match

    def test_consecutive_bonus(self):
        s1 = _fuzzy_score("ma", "main.py")
        s2 = _fuzzy_score("mn", "main.py")
        assert s1 > s2  # "ma" is consecutive, "mn" is not

    def test_shorter_path_preferred(self):
        s1 = _fuzzy_score("readme", "README.md")
        s2 = _fuzzy_score("readme", "docs/subdir/README.md")
        assert s1 > s2

    def test_boundary_bonus(self):
        s1 = _fuzzy_score("t", "test.py")  # at start
        s2 = _fuzzy_score("t", "boost.py")  # mid-word
        assert s1 > s2

    def test_empty_query(self):
        # Empty query matches everything trivially
        score = _fuzzy_score("", "anything.py")
        assert score >= 0


# ---------------------------------------------------------------------------
# ProjectFileIndex
# ---------------------------------------------------------------------------


class TestProjectFileIndex:
    def test_git_repo(self, tmp_path):
        """In a git repo, git ls-files populates the index."""
        # Create a git repo with some files
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        (tmp_path / "hello.py").write_text("# hello")
        (tmp_path / "world.txt").write_text("world")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)

        idx = ProjectFileIndex(tmp_path, ttl_seconds=0)
        files = idx.get_files()
        assert "hello.py" in files
        assert "world.txt" in files

    def test_non_git_fallback(self, tmp_path):
        """Without git, falls back to directory walk."""
        (tmp_path / "app.py").write_text("# app")
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "util.py").write_text("# util")

        idx = ProjectFileIndex(tmp_path, ttl_seconds=0)
        files = idx.get_files()
        assert "app.py" in files
        assert os.path.join("lib", "util.py") in files

    def test_search_fuzzy(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        (tmp_path / "README.md").write_text("# readme")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# main")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)

        idx = ProjectFileIndex(tmp_path, ttl_seconds=0)
        results = idx.search("main")
        assert any("main" in r for r in results)

    def test_search_empty_returns_files(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.py").write_text("b")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)

        idx = ProjectFileIndex(tmp_path, ttl_seconds=0)
        results = idx.search("")
        assert len(results) >= 2

    def test_cache_ttl(self, tmp_path):
        (tmp_path / "init.py").write_text("")
        idx = ProjectFileIndex(tmp_path, ttl_seconds=9999)
        files1 = idx.get_files()
        # Add another file — should NOT appear due to cache
        (tmp_path / "new.py").write_text("")
        files2 = idx.get_files()
        assert files1 == files2

    def test_excludes_git_dir(self, tmp_path):
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
        (tmp_path / "app.py").write_text("")
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)

        idx = ProjectFileIndex(tmp_path, ttl_seconds=0)
        files = idx.get_files()
        assert not any(".git" in f for f in files)


# ---------------------------------------------------------------------------
# AtMentionCompleter
# ---------------------------------------------------------------------------


class TestAtMentionCompleter:
    def _make_completer(self, files=None, agents=None):
        """Create a completer with a mock file index."""

        class MockFileIndex:
            def __init__(self, file_list):
                self._files = file_list or []

            def search(self, query, *, max_results=15):
                if not query:
                    return self._files[:max_results]
                ql = query.lower()
                return [f for f in self._files if ql in f.lower()][:max_results]

        return AtMentionCompleter(
            file_index=MockFileIndex(files or []),
            agent_names=agents,
        )

    def _completions(self, completer, text):
        doc = Document(text, cursor_position=len(text))
        return list(completer.get_completions(doc, CompleteEvent()))

    @staticmethod
    def _display_texts(results):
        """Extract plain display strings from Completion objects."""
        texts = []
        for r in results:
            if hasattr(r.display, "__iter__") and not isinstance(r.display, str):
                # FormattedText — extract the text parts
                texts.append("".join(t[1] for t in r.display))
            else:
                texts.append(str(r.display))
        return texts

    def test_at_shows_agents_and_files(self):
        c = self._make_completer(
            files=["README.md", "main.py"],
            agents=[("code-reviewer", "Review code")],
        )
        results = self._completions(c, "@")
        displays = self._display_texts(results)
        assert "* code-reviewer" in displays
        assert "+ README.md" in displays
        assert "+ main.py" in displays

    def test_agent_prefix_filter(self):
        c = self._make_completer(
            files=["README.md"],
            agents=[("code-reviewer", "Review code"), ("planner", "Plan tasks")],
        )
        results = self._completions(c, "@code")
        displays = self._display_texts(results)
        assert "* code-reviewer" in displays
        assert "* planner" not in displays

    def test_file_fuzzy_search(self):
        c = self._make_completer(
            files=["src/main.py", "src/utils.py", "README.md"],
            agents=[],
        )
        results = self._completions(c, "@main")
        displays = self._display_texts(results)
        assert "+ src/main.py" in displays
        assert "+ README.md" not in displays

    def test_no_trigger_without_at(self):
        c = self._make_completer(files=["main.py"], agents=[("test", "desc")])
        results = self._completions(c, "hello world")
        assert results == []

    def test_no_trigger_email_style(self):
        c = self._make_completer(files=["main.py"])
        results = self._completions(c, "user@host")
        assert results == []

    def test_at_after_space(self):
        c = self._make_completer(files=["main.py"])
        results = self._completions(c, "check @main")
        displays = self._display_texts(results)
        assert "+ main.py" in displays

    def test_completion_text_has_at_prefix(self):
        c = self._make_completer(files=["main.py"], agents=[])
        results = self._completions(c, "@main")
        assert results[0].text.startswith("@")

    def test_quoted_file_path(self):
        c = self._make_completer(files=["my file.py"], agents=[])
        results = self._completions(c, "@")
        # File with space should get quoted
        assert any('"' in r.text for r in results)

    def test_max_completions(self):
        files = [f"file{i}.py" for i in range(30)]
        c = self._make_completer(files=files, agents=[])
        results = self._completions(c, "@")
        assert len(results) <= 15


# ---------------------------------------------------------------------------
# extract_at_file_mentions
# ---------------------------------------------------------------------------


class TestExtractAtFileMentions:
    def test_simple_mention(self):
        paths = extract_at_file_mentions("check @src/main.py please")
        assert paths == ["src/main.py"]

    def test_quoted_mention(self):
        paths = extract_at_file_mentions('look at @"path with spaces.py" here')
        assert paths == ["path with spaces.py"]

    def test_multiple_mentions(self):
        paths = extract_at_file_mentions("compare @a.py and @b.py")
        assert paths == ["a.py", "b.py"]

    def test_excludes_agent_names(self):
        paths = extract_at_file_mentions(
            "ask @code-reviewer about this",
            active_agent_names={"code-reviewer"},
        )
        assert paths == []

    def test_excludes_agent_marker(self):
        paths = extract_at_file_mentions('send @"reviewer (agent)" a message')
        assert paths == []

    def test_excludes_agent_dash_prefix(self):
        paths = extract_at_file_mentions("@agent-reviewer do this")
        assert paths == []

    def test_no_mentions(self):
        paths = extract_at_file_mentions("no mentions here")
        assert paths == []

    def test_at_start(self):
        paths = extract_at_file_mentions("@README.md explain")
        assert paths == ["README.md"]


# ---------------------------------------------------------------------------
# process_at_file_mentions
# ---------------------------------------------------------------------------


class TestProcessAtFileMentions:
    def test_inlines_file_content(self, tmp_path):
        (tmp_path / "hello.py").write_text("print('hello')")
        result = process_at_file_mentions("explain @hello.py", cwd=tmp_path)
        assert '<file path="hello.py">' in result
        assert "print('hello')" in result
        assert "User request: explain @hello.py" in result

    def test_no_mentions_passthrough(self, tmp_path):
        result = process_at_file_mentions("just a question", cwd=tmp_path)
        assert result == "just a question"

    def test_missing_file(self, tmp_path):
        result = process_at_file_mentions("check @missing.py", cwd=tmp_path)
        assert "[File not found: missing.py]" in result

    def test_directory_mention(self, tmp_path):
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "a.py").write_text("")
        (tmp_path / "subdir" / "b.py").write_text("")
        result = process_at_file_mentions("list @subdir", cwd=tmp_path)
        assert 'type="directory"' in result
        assert "a.py" in result

    def test_truncates_large_file(self, tmp_path):
        (tmp_path / "big.txt").write_text("x" * 200_000)
        result = process_at_file_mentions("read @big.txt", cwd=tmp_path)
        assert "truncated" in result

    def test_excludes_agents(self, tmp_path):
        (tmp_path / "code-reviewer").write_text("some file")
        result = process_at_file_mentions(
            "ask @code-reviewer for help",
            cwd=tmp_path,
            active_agent_names={"code-reviewer"},
        )
        # Agent mention excluded — no file inlining
        assert result == "ask @code-reviewer for help"
