"""Tests for prompt suggestion engine."""

from koder_agent.harness.prompt_suggestion import (
    PromptSuggestionEngine,
    normalize_prompt_suggestion,
    prompt_suggestions_enabled,
)


class TestHistorySuggestion:
    """Tests for history-based suggestions."""

    def test_matches_prefix(self):
        engine = PromptSuggestionEngine(["fix the bug", "run tests", "add feature"])
        assert engine.suggest_from_history("fix") == "fix the bug"

    def test_returns_most_recent(self):
        engine = PromptSuggestionEngine(["fix old bug", "fix new bug"])
        assert engine.suggest_from_history("fix") == "fix new bug"

    def test_no_match_returns_none(self):
        engine = PromptSuggestionEngine(["fix the bug"])
        assert engine.suggest_from_history("deploy") is None

    def test_empty_prefix_returns_none(self):
        engine = PromptSuggestionEngine(["fix the bug"])
        assert engine.suggest_from_history("") is None

    def test_case_insensitive(self):
        engine = PromptSuggestionEngine(["Fix the authentication bug"])
        assert engine.suggest_from_history("fix") == "Fix the authentication bug"

    def test_empty_history(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_history("Fix") is None


class TestContextSuggestion:
    """Tests for context-based suggestions."""

    def test_file_mention(self):
        engine = PromptSuggestionEngine([])
        result = engine.suggest_from_context("I found an issue in src/main.py")
        assert result is not None and "Read" in result

    def test_test_mention(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_context("All tests are passing") == "Run the tests"

    def test_error_mention(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_context("There's an error in the code") == "Fix the error"

    def test_build_mention(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_context("The build output shows") == "Build the project"

    def test_lint_mention(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_context("Running ruff check found issues") == "Run the linter"

    def test_format_mention(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_context("The formatting looks off") == "Run the linter"

    def test_deploy_mention(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_context("Ready to deploy") == "Check deployment status"

    def test_release_mention(self):
        engine = PromptSuggestionEngine([])
        assert (
            engine.suggest_from_context("Time to publish the release") == "Check deployment status"
        )

    def test_review_mention(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_context("Please review the pull request") == "Review the changes"

    def test_install_mention(self):
        engine = PromptSuggestionEngine([])
        assert (
            engine.suggest_from_context("Need to install the dependency") == "Install dependencies"
        )

    def test_commit_mention(self):
        engine = PromptSuggestionEngine([])
        assert (
            engine.suggest_from_context("Changes are staged and ready to commit")
            == "Commit the changes"
        )

    def test_doc_mention(self):
        engine = PromptSuggestionEngine([])
        assert (
            engine.suggest_from_context("The documentation needs updating")
            == "Update the documentation"
        )

    def test_no_pattern_returns_none(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_context("Hello world") is None

    def test_empty_message_returns_none(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_from_context("") is None

    def test_file_path_prioritized(self):
        engine = PromptSuggestionEngine([])
        message = "There's an error in src/main.py that needs testing"
        assert engine.suggest_from_context(message) == "Read src/main.py"


class TestSlashCommandSuggestion:
    """Tests for slash command auto-completion."""

    def test_slash_help(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_slash_command("/he") == "/help"

    def test_slash_compact(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_slash_command("/com") == "/compact"

    def test_slash_diff(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_slash_command("/di") == "/diff"

    def test_slash_model(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_slash_command("/mo") == "/model"

    def test_slash_commit(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_slash_command("/co") == "/compact"

    def test_slash_no_match(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_slash_command("/xyz") is None

    def test_not_slash_returns_none(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_slash_command("help") is None

    def test_bare_slash_returns_none(self):
        engine = PromptSuggestionEngine([])
        assert engine.suggest_slash_command("/") is None


class TestGetSuggestion:
    """Tests for the combined get_suggestion method."""

    def test_slash_command_priority(self):
        engine = PromptSuggestionEngine(["/help me"])
        result = engine.get_suggestion("/he")
        assert result == "/help"  # Slash command over history

    def test_history_over_context(self):
        engine = PromptSuggestionEngine(["fix the bug"])
        result = engine.get_suggestion("fix", last_assistant="error found")
        assert result == "fix the bug"

    def test_falls_back_to_context(self):
        engine = PromptSuggestionEngine([])
        result = engine.get_suggestion("", last_assistant="The test suite failed")
        assert result == "Run the tests"

    def test_returns_none_when_no_match(self):
        engine = PromptSuggestionEngine(["fix the bug"])
        assert engine.get_suggestion("Delete", "Just a regular message") is None

    def test_none_last_assistant(self):
        engine = PromptSuggestionEngine(["fix the bug"])
        assert engine.get_suggestion("fix", None) == "fix the bug"
        assert engine.get_suggestion("Delete", None) is None

    def test_empty_prefix_uses_context(self):
        engine = PromptSuggestionEngine(["fix the bug"])
        assert engine.get_suggestion("", "Run the tests") == "Run the tests"


class TestPostTurnSuggestion:
    """Tests for local post-turn prompt suggestions."""

    def test_suggest_next_prompt_uses_recent_output(self):
        engine = PromptSuggestionEngine([])

        assert engine.suggest_next_prompt("fix the bug", "The test suite failed") == "Run the tests"

    def test_suggest_next_prompt_uses_user_intent_when_output_is_quiet(self):
        engine = PromptSuggestionEngine([])

        assert engine.suggest_next_prompt("fix this and run tests", "Implemented the fix") == (
            "Run the tests"
        )

    def test_filters_assistant_voice_suggestions(self):
        assert normalize_prompt_suggestion("I'll run the tests") is None
        assert normalize_prompt_suggestion("looks good") is None
        assert normalize_prompt_suggestion("run the tests") == "run the tests"

    def test_env_can_disable_prompt_suggestions(self, monkeypatch):
        monkeypatch.setenv("KODER_ENABLE_PROMPT_SUGGESTION", "false")

        assert prompt_suggestions_enabled() is False
