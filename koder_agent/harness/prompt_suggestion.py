"""Prompt suggestion engine for ghost text auto-completion."""

from __future__ import annotations

import os
import re
from typing import Optional

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_ALLOWED_SINGLE_WORDS = {
    "yes",
    "yeah",
    "yep",
    "sure",
    "ok",
    "okay",
    "no",
    "continue",
    "stop",
    "push",
    "commit",
    "deploy",
    "check",
    "exit",
    "quit",
}


def prompt_suggestions_enabled() -> bool:
    """Return whether post-turn prompt suggestions are enabled."""

    value = os.environ.get("KODER_ENABLE_PROMPT_SUGGESTION")
    if value is None:
        return True
    return value.strip().lower() not in _FALSE_VALUES


def normalize_prompt_suggestion(suggestion: Optional[str]) -> Optional[str]:
    """Clean and filter a generated prompt suggestion."""

    if suggestion is None:
        return None
    cleaned = " ".join(suggestion.strip().split())
    if not cleaned or should_filter_prompt_suggestion(cleaned):
        return None
    return cleaned.rstrip(".")


def should_filter_prompt_suggestion(suggestion: str) -> bool:
    """Return True when a suggestion should not be shown to the user."""

    stripped = suggestion.strip()
    lower = stripped.lower()
    words = stripped.split()
    word_count = len(words)

    if lower == "done":
        return True
    if lower in {"nothing found", "nothing found."}:
        return True
    if lower.startswith("nothing to suggest") or lower.startswith("no suggestion"):
        return True
    if re.search(r"\bsilence is\b|\bstay(s|ing)? silent\b", lower):
        return True
    if re.fullmatch(r"\W*silence\W*", lower):
        return True
    if re.fullmatch(r"\(.*\)|\[.*\]", stripped):
        return True
    if lower.startswith(("api error:", "prompt is too long", "request timed out")):
        return True
    if re.match(r"^\w+:\s", stripped):
        return True
    if word_count == 1 and not stripped.startswith("/") and lower not in _ALLOWED_SINGLE_WORDS:
        return True
    if word_count > 12 or len(stripped) >= 100:
        return True
    if re.search(r"[.!?]\s+[A-Z]", stripped):
        return True
    if "\n" in stripped or "**" in stripped:
        return True
    if re.search(
        r"thanks|thank you|looks good|sounds good|that works|nice|great|perfect|awesome|excellent",
        lower,
    ):
        return True
    if re.match(
        r"^(let me|i'll|i've|i'm|i can|i would|i think|here's|here is|here are|you can|you should|you could|sure,|of course|certainly)\b",
        stripped,
        flags=re.IGNORECASE,
    ):
        return True
    return False


class PromptSuggestionEngine:
    """Engine for generating prompt suggestions based on history and context."""

    def __init__(self, history: list[str]) -> None:
        """
        Initialize the prompt suggestion engine.

        Args:
            history: List of previous user prompts, ordered from oldest to newest.
        """
        self.history = history

    def suggest_from_history(self, prefix: str) -> Optional[str]:
        """
        Find the best history match for a given prefix.

        Returns the most recent history item that starts with the prefix
        (case-insensitive).

        Args:
            prefix: The prefix to match against history.

        Returns:
            The matching history item, or None if no match found.
        """
        if not prefix or not self.history:
            return None

        prefix_lower = prefix.lower()

        # Iterate in reverse to get the most recent match
        for item in reversed(self.history):
            if item.lower().startswith(prefix_lower):
                return item

        return None

    def suggest_from_context(self, last_assistant_message: str) -> Optional[str]:
        """
        Generate a simple follow-up suggestion based on the last assistant message.

        Uses heuristic pattern matching to detect common scenarios:
        - File mentions → "Read {file}"
        - Test mentions → "Run the tests"
        - Error/bug mentions → "Fix the error"
        - Build mentions → "Build the project"

        Args:
            last_assistant_message: The last message from the assistant.

        Returns:
            A suggested follow-up prompt, or None if no pattern matches.
        """
        if not last_assistant_message:
            return None

        message_lower = last_assistant_message.lower()

        # Pattern 1: File path detection (highest priority)
        # Match common file extensions and paths
        file_pattern = r"\b([a-zA-Z0-9_/.-]+\.[a-zA-Z0-9]+)\b"
        file_matches = re.findall(file_pattern, last_assistant_message)

        # Filter for valid-looking file paths (contain / or common extensions)
        valid_files = [
            f
            for f in file_matches
            if "/" in f
            or any(
                f.endswith(ext)
                for ext in [
                    ".py",
                    ".js",
                    ".ts",
                    ".jsx",
                    ".tsx",
                    ".md",
                    ".json",
                    ".yaml",
                    ".yml",
                    ".txt",
                    ".sh",
                    ".bash",
                    ".go",
                    ".rs",
                    ".java",
                    ".c",
                    ".cpp",
                    ".h",
                    ".css",
                    ".html",
                    ".xml",
                    ".toml",
                    ".ini",
                    ".cfg",
                    ".conf",
                ]
            )
        ]

        if valid_files:
            # Return the first valid file path
            return f"Read {valid_files[0]}"

        # Pattern 2: Test mentions
        test_keywords = ["test", "pytest", "testing", "tested"]
        if any(keyword in message_lower for keyword in test_keywords):
            return "Run the tests"

        # Pattern 3: Error/bug/failure mentions
        error_keywords = ["error", "bug", "failure", "failed", "fail"]
        if any(keyword in message_lower for keyword in error_keywords):
            return "Fix the error"

        # Pattern 4: Build/compile mentions
        build_keywords = ["build", "compile", "compilation"]
        if any(keyword in message_lower for keyword in build_keywords):
            return "Build the project"

        # Pattern 5: Lint/format mentions
        lint_keywords = [
            "lint",
            "linting",
            "format",
            "formatting",
            "ruff",
            "black",
            "eslint",
            "prettier",
        ]
        if any(keyword in message_lower for keyword in lint_keywords):
            return "Run the linter"

        # Pattern 6: Deploy/release mentions
        deploy_keywords = ["deploy", "deployment", "release", "publish", "ship"]
        if any(keyword in message_lower for keyword in deploy_keywords):
            return "Check deployment status"

        # Pattern 7: Review/PR mentions
        review_keywords = ["review", "pull request", "pr ", "merge"]
        if any(keyword in message_lower for keyword in review_keywords):
            return "Review the changes"

        # Pattern 8: Install/setup mentions
        install_keywords = ["install", "setup", "configure", "dependency", "dependencies"]
        if any(keyword in message_lower for keyword in install_keywords):
            return "Install dependencies"

        # Pattern 9: Commit mentions
        commit_keywords = ["commit", "committed", "staged", "changes ready"]
        if any(keyword in message_lower for keyword in commit_keywords):
            return "Commit the changes"

        # Pattern 10: Documentation mentions
        doc_keywords = ["document", "documentation", "readme", "docs"]
        if any(keyword in message_lower for keyword in doc_keywords):
            return "Update the documentation"

        return None

    def suggest_slash_command(self, prefix: str) -> Optional[str]:
        """Suggest a slash command based on prefix.

        When the user types '/' followed by characters, suggest matching commands.

        Args:
            prefix: The current input prefix (must start with '/').

        Returns:
            A matching slash command, or None if no match found.
        """
        if not prefix.startswith("/"):
            return None

        partial = prefix[1:].lower()
        if not partial:
            return None

        common_commands = [
            "/help",
            "/clear",
            "/compact",
            "/diff",
            "/status",
            "/model",
            "/config",
            "/vim",
            "/theme",
            "/export",
            "/plan",
            "/review",
            "/commit",
            "/branch",
            "/resume",
            "/skills",
            "/plugin",
            "/mcp",
            "/doctor",
            "/agents",
            "/hooks",
            "/memory",
            "/files",
            "/context",
            "/cost",
            "/usage",
            "/effort",
            "/voice",
            "/buddy",
            "/env",
            "/add-dir",
            "/init",
        ]

        for cmd in common_commands:
            if cmd[1:].startswith(partial):
                return cmd

        return None

    def get_suggestion(self, prefix: str, last_assistant: Optional[str] = None) -> Optional[str]:
        """
        Get a suggestion, trying slash commands, then history, then context-based.

        Args:
            prefix: The current input prefix to match against history.
            last_assistant: The last assistant message for context-based suggestions.

        Returns:
            A suggested prompt, or None if no suggestion available.
        """
        # Try slash command suggestion first
        if prefix.startswith("/"):
            slash = self.suggest_slash_command(prefix)
            if slash:
                return slash

        # Try history match if we have a prefix
        if prefix:
            history_match = self.suggest_from_history(prefix)
            if history_match:
                return history_match

        # Fall back to context-based suggestion
        if last_assistant:
            return normalize_prompt_suggestion(self.suggest_from_context(last_assistant))

        return None

    def suggest_next_prompt(
        self,
        user_input: str,
        last_assistant: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a local next-prompt suggestion after a completed turn."""

        context_suggestion = None
        if last_assistant:
            context_suggestion = self.suggest_from_context(last_assistant)
        if context_suggestion:
            return normalize_prompt_suggestion(context_suggestion)

        intent_suggestion = self.suggest_from_user_intent(user_input, last_assistant or "")
        return normalize_prompt_suggestion(intent_suggestion)

    def suggest_from_user_intent(
        self,
        user_input: str,
        last_assistant_message: str,
    ) -> Optional[str]:
        """Infer an obvious next step from the recent user request."""

        if not user_input:
            return None

        user_lower = user_input.lower()
        assistant_lower = last_assistant_message.lower()

        if "test" in user_lower or "pytest" in user_lower:
            if not any(word in assistant_lower for word in ["test", "pytest", "suite"]):
                return "Run the tests"
        if "commit" in user_lower and any(
            word in assistant_lower for word in ["ready", "passed", "done", "complete"]
        ):
            return "Commit the changes"
        if any(word in user_lower for word in ["doc", "readme", "documentation"]):
            if any(word in assistant_lower for word in ["code", "implemented", "updated"]):
                return "Update the documentation"
        return None
