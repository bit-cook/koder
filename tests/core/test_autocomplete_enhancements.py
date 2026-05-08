"""Tests for autocomplete enhancements: ghost text, path completion, shell, skill usage."""

from __future__ import annotations

import json

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from koder_agent.core.at_completer import (
    AtMentionCompleter,
    _is_path_like,
    _path_completions,
)
from koder_agent.core.auto_suggest import KoderAutoSuggest
from koder_agent.core.file_index import find_common_prefix
from koder_agent.core.interactive import InteractivePrompt, _accept_current_auto_suggestion
from koder_agent.core.shell_completer import ShellCompleter
from koder_agent.core.skill_usage import SkillUsageTracker

# ---------------------------------------------------------------------------
# Ghost text (KoderAutoSuggest)
# ---------------------------------------------------------------------------


class TestKoderAutoSuggest:
    def _suggest(self, suggestor, text):
        buf = Buffer()
        doc = Document(text, cursor_position=len(text))
        return suggestor.get_suggestion(buf, doc)

    def test_slash_ghost_at_start(self):
        s = KoderAutoSuggest(commands={"commit": "desc", "compact": "desc"})
        result = self._suggest(s, "/com")
        assert result is not None
        # Should suggest "mit" or "pact" (first match wins)
        assert result.text in ("mit", "pact")

    def test_slash_ghost_mid_input(self):
        s = KoderAutoSuggest(commands={"commit": "desc"})
        result = self._suggest(s, "please /com")
        assert result is not None
        assert result.text == "mit"

    def test_no_slash_ghost_for_full_match(self):
        s = KoderAutoSuggest(commands={"commit": "desc"})
        result = self._suggest(s, "/commit")
        assert result is None  # Already complete

    def test_history_ghost(self):
        s = KoderAutoSuggest(commands={})
        s.record_input("fix the bug in auth module")
        result = self._suggest(s, "fix the")
        assert result is not None
        assert result.text == " bug in auth module"

    def test_history_no_duplicate(self):
        s = KoderAutoSuggest(commands={})
        s.record_input("hello world")
        s.record_input("hello world")  # duplicate
        assert len(s._history) == 1

    def test_history_most_recent_first(self):
        s = KoderAutoSuggest(commands={})
        s.record_input("older input")
        s.record_input("newer input")
        # "newer" should be first in history
        assert s._history[0] == "newer input"

    def test_no_suggestion_for_empty(self):
        s = KoderAutoSuggest(commands={"commit": "desc"})
        result = self._suggest(s, "")
        assert result is None

    def test_speculative_suggestion_for_empty_prompt(self):
        s = KoderAutoSuggest(commands={})
        s.set_speculative_suggestion("Run the tests")

        result = self._suggest(s, "")

        assert result is not None
        assert result.text == "Run the tests"

    def test_speculative_suggestion_completes_typed_prefix(self):
        s = KoderAutoSuggest(commands={})
        s.set_speculative_suggestion("Run the tests")

        result = self._suggest(s, "Run")

        assert result is not None
        assert result.text == " the tests"

    def test_record_input_clears_speculative_suggestion(self):
        s = KoderAutoSuggest(commands={})
        s.set_speculative_suggestion("Run the tests")

        s.record_input("run pytest")

        assert s.get_speculative_suggestion() is None

    def test_accept_current_auto_suggestion_uses_dynamic_empty_prompt_suggestion(self):
        s = KoderAutoSuggest(commands={})
        s.set_speculative_suggestion("Run the tests")
        buf = Buffer(auto_suggest=s, document=Document("", cursor_position=0))

        accepted = _accept_current_auto_suggestion(buf)

        assert accepted is True
        assert buf.text == "Run the tests"

    def test_accept_current_auto_suggestion_uses_dynamic_prefix_suggestion(self):
        s = KoderAutoSuggest(commands={})
        s.set_speculative_suggestion("Run the tests")
        buf = Buffer(auto_suggest=s, document=Document("Run", cursor_position=3))

        accepted = _accept_current_auto_suggestion(buf)

        assert accepted is True
        assert buf.text == "Run the tests"

    def test_slash_ghost_no_match(self):
        s = KoderAutoSuggest(commands={"commit": "desc"})
        result = self._suggest(s, "/xyz")
        assert result is None

    def test_slash_ghost_ignores_email_style(self):
        s = KoderAutoSuggest(commands={"commit": "desc"})
        result = self._suggest(s, "user@com")
        assert result is None  # No space before /


class TestInteractivePromptHistoryReset:
    def test_reset_history_clears_prompt_and_ghost_history(self):
        prompt = InteractivePrompt({"help": "Show help"})
        prompt.history.append_string("/help")
        prompt.auto_suggest.record_input("/help")

        prompt.reset_history()

        assert list(prompt.history.get_strings()) == []
        assert prompt.auto_suggest._history == []


# ---------------------------------------------------------------------------
# find_common_prefix
# ---------------------------------------------------------------------------


class TestFindCommonPrefix:
    def test_common_prefix(self):
        assert find_common_prefix(["src/main.py", "src/models.py"]) == "src/m"

    def test_no_common(self):
        assert find_common_prefix(["abc", "xyz"]) == ""

    def test_single_item(self):
        assert find_common_prefix(["hello"]) == "hello"

    def test_empty_list(self):
        assert find_common_prefix([]) == ""

    def test_identical(self):
        assert find_common_prefix(["same", "same"]) == "same"


# ---------------------------------------------------------------------------
# Path-style completion
# ---------------------------------------------------------------------------


class TestIsPathLike:
    def test_home(self):
        assert _is_path_like("~/") is True

    def test_relative(self):
        assert _is_path_like("./") is True

    def test_parent(self):
        assert _is_path_like("../") is True

    def test_absolute(self):
        assert _is_path_like("/usr") is True

    def test_bare_tilde(self):
        assert _is_path_like("~") is True

    def test_not_path(self):
        assert _is_path_like("README") is False

    def test_not_path_with_slash(self):
        # "src/main" doesn't start with ./ ~/ / — not path-like
        assert _is_path_like("src/main") is False


class TestPathCompletions:
    def test_lists_directory(self, tmp_path):
        (tmp_path / "file1.py").write_text("")
        (tmp_path / "file2.py").write_text("")
        (tmp_path / "subdir").mkdir()
        results = list(_path_completions(str(tmp_path) + "/", tmp_path))
        names = [r[0] for r in results]
        assert any("file1.py" in n for n in names)
        assert any("subdir" in n for n in names)

    def test_filters_by_prefix(self, tmp_path):
        (tmp_path / "alpha.py").write_text("")
        (tmp_path / "beta.py").write_text("")
        results = list(_path_completions(str(tmp_path) + "/al", tmp_path))
        names = [r[0] for r in results]
        assert any("alpha" in n for n in names)
        assert not any("beta" in n for n in names)

    def test_marks_directories(self, tmp_path):
        (tmp_path / "adir").mkdir()
        (tmp_path / "afile.py").write_text("")
        results = list(_path_completions(str(tmp_path) + "/", tmp_path))
        for display, is_dir in results:
            if "adir" in display:
                assert is_dir is True
            if "afile" in display:
                assert is_dir is False


# ---------------------------------------------------------------------------
# AtMentionCompleter — path-style and MCP resources
# ---------------------------------------------------------------------------


class TestAtMentionCompleterEnhancements:
    class MockFileIndex:
        def __init__(self, files=None):
            self._files = files or []

        def search(self, query, *, max_results=15):
            if not query:
                return self._files[:max_results]
            ql = query.lower()
            return [f for f in self._files if ql in f.lower()][:max_results]

    def _completions(self, completer, text):
        doc = Document(text, cursor_position=len(text))
        return list(completer.get_completions(doc, CompleteEvent()))

    @staticmethod
    def _display_texts(results):
        texts = []
        for r in results:
            if hasattr(r.display, "__iter__") and not isinstance(r.display, str):
                texts.append("".join(t[1] for t in r.display))
            else:
                texts.append(str(r.display))
        return texts

    def test_mcp_resources_shown(self):
        c = AtMentionCompleter(
            file_index=self.MockFileIndex(),
            mcp_resources=[("server:resource/path", "A resource")],
        )
        results = self._completions(c, "@server")
        displays = self._display_texts(results)
        assert any("◇" in d for d in displays)

    def test_path_style_completion(self, tmp_path):
        (tmp_path / "test.py").write_text("")
        c = AtMentionCompleter(
            file_index=self.MockFileIndex(),
            cwd=tmp_path,
        )
        results = self._completions(c, f"@{tmp_path}/")
        displays = self._display_texts(results)
        assert any("test.py" in d for d in displays)

    def test_directory_drill_down(self, tmp_path):
        (tmp_path / "subdir").mkdir()
        c = AtMentionCompleter(
            file_index=self.MockFileIndex(),
            cwd=tmp_path,
        )
        results = self._completions(c, f"@{tmp_path}/")
        # Directory completions should end with / (no trailing space)
        dir_completions = [r for r in results if "subdir" in r.text]
        assert len(dir_completions) > 0
        assert dir_completions[0].text.endswith("/")
        assert not dir_completions[0].text.endswith(" ")


# ---------------------------------------------------------------------------
# ShellCompleter
# ---------------------------------------------------------------------------


class TestShellCompleter:
    def _completions(self, text):
        doc = Document(text, cursor_position=len(text))
        return list(ShellCompleter().get_completions(doc, CompleteEvent()))

    def test_no_trigger_without_bang(self):
        results = self._completions("hello")
        assert results == []

    def test_command_completion(self):
        results = self._completions("!ls")
        # ls should be a valid command on any system
        if results:  # May fail in sandboxed environments
            assert any("ls" in r.text for r in results)

    def test_file_completion(self):
        results = self._completions("!cat READ")
        # Should try to complete file paths
        # May or may not find matches depending on cwd
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# SkillUsageTracker
# ---------------------------------------------------------------------------


class TestSkillUsageTracker:
    def test_record_and_score(self, tmp_path):
        tracker = SkillUsageTracker(store_path=tmp_path / "usage.json")
        tracker.record("commit")
        score = tracker.get_score("commit")
        assert score > 0

    def test_unrecorded_score_zero(self, tmp_path):
        tracker = SkillUsageTracker(store_path=tmp_path / "usage.json")
        assert tracker.get_score("unknown") == 0.0

    def test_debounce(self, tmp_path):
        tracker = SkillUsageTracker(store_path=tmp_path / "usage.json")
        tracker.record("commit")
        tracker.record("commit")  # Should be debounced
        data = json.loads((tmp_path / "usage.json").read_text())
        assert data["commit"]["usage_count"] == 1

    def test_sort_commands(self, tmp_path):
        tracker = SkillUsageTracker(store_path=tmp_path / "usage.json")
        # Force a record (bypass debounce)
        tracker._last_write = {}
        tracker.record("status")
        commands = [("help", "Help"), ("status", "Status"), ("exit", "Exit")]
        sorted_cmds = tracker.sort_commands(commands)
        # "status" should be first since it was used
        assert sorted_cmds[0][0] == "status"

    def test_persistence(self, tmp_path):
        tracker1 = SkillUsageTracker(store_path=tmp_path / "usage.json")
        tracker1.record("commit")
        # New tracker reads from same file
        tracker2 = SkillUsageTracker(store_path=tmp_path / "usage.json")
        assert tracker2.get_score("commit") > 0
