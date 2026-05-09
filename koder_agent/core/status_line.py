"""Status line UI component for displaying model, session, and usage info."""

import json
import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Optional

from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.layout import FormattedTextControl, Window

from ..harness.statusline_settings import resolve_statusline_config
from ..harness.version_info import resolve_runtime_version
from ..utils.model_info import get_context_window_size

if TYPE_CHECKING:
    from ..harness.pr_status import PrStatusPoller
    from .usage_tracker import UsageTracker

# Review-state -> prompt_toolkit style class mapping.
_PR_STATE_STYLES: dict[str, str] = {
    "approved": "class:status-pr-approved",
    "pending": "class:status-pr-pending",
    "changes_requested": "class:status-pr-changes-requested",
    "draft": "class:status-pr-draft",
    "merged": "class:status-pr-merged",
}

_PR_STATE_LABELS: dict[str, str] = {
    "approved": "approved",
    "pending": "pending",
    "changes_requested": "changes requested",
    "draft": "draft",
    "merged": "merged",
}


class StatusLine:
    """
    Status line component showing model, directory, session, tokens, cost, and context usage.

    The status line is rendered below the input box and updates dynamically.
    """

    def __init__(
        self,
        usage_tracker: "UsageTracker",
        session_id: str,
    ):
        """
        Initialize the status line.

        Args:
            usage_tracker: UsageTracker instance for token/cost data
            session_id: Current session identifier
        """
        self.usage_tracker = usage_tracker
        self.session_id = session_id
        self._display_name: Optional[str] = None  # AI-generated display name
        self._notice: Optional[str] = None
        self.pr_poller: Optional["PrStatusPoller"] = None
        self._project_dir = os.getcwd()
        self._cached_command_signature: str | None = None
        self._cached_command_output: str | None = None

    def _format_tokens(self, n: int) -> str:
        """Format token count with k/M suffix for readability."""
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        elif n >= 1000:
            # Remove trailing .0 for cleaner display (e.g., "200k" not "200.0k")
            val = n / 1000
            if val == int(val):
                return f"{int(val)}k"
            return f"{val:.1f}k"
        return str(n)

    def _truncate(self, s: str, max_len: int, from_start: bool = False) -> str:
        """Truncate string with ellipsis."""
        if max_len <= 0:
            return ""
        if max_len <= 3:
            return s[:max_len]
        if len(s) <= max_len:
            return s
        if from_start:
            return s[: max_len - 3] + "..."
        return "..." + s[-(max_len - 3) :]

    def _terminal_columns(self) -> int:
        """Return the current terminal width with a conservative fallback."""
        try:
            return shutil.get_terminal_size((100, 24)).columns
        except Exception:
            return 100

    def _compact_cwd(self, cwd: str, max_len: int) -> str:
        """Render the cwd like Claude's compact status line: stable and short."""
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home) :]
        if len(cwd) <= max_len:
            return cwd

        basename = os.path.basename(cwd.rstrip(os.sep)) or cwd
        parent = os.path.basename(os.path.dirname(cwd.rstrip(os.sep)))
        compact = f".../{parent}/{basename}" if parent else basename
        if len(compact) <= max_len:
            return compact
        return self._truncate(cwd, max_len)

    def _append_field(
        self,
        fragments: list[tuple[str, str]],
        label: str,
        value: str,
        *,
        value_style: str = "class:status-value",
    ) -> None:
        if fragments:
            fragments.append(("class:status-separator", " | "))
        else:
            fragments.append(("", " "))
        fragments.extend(
            [
                ("class:status-label", label),
                (value_style, value),
            ]
        )

    def _get_context_style(self, percentage: float) -> str:
        """Get style class based on context usage percentage."""
        if percentage >= 90:
            return "class:status-context-critical"
        elif percentage >= 70:
            return "class:status-context-warn"
        return "class:status-context-ok"

    def _build_statusline_payload(self) -> dict[str, object]:
        usage = self.usage_tracker.session_usage
        model = self.usage_tracker.model
        current_dir = os.getcwd()
        max_context = get_context_window_size(model)
        used_percentage = (
            (usage.current_context_tokens / max_context * 100) if max_context > 0 else 0.0
        )
        added_dirs = [
            item
            for item in str(os.environ.get("KODER_ADDITIONAL_DIRS", "")).split(os.pathsep)
            if item
        ]
        return {
            "session_id": self.session_id,
            "session_name": self._display_name,
            "cwd": current_dir,
            "transcript_path": None,
            "model": {
                "id": model,
                "display_name": model.replace("litellm/", "").split("/")[-1],
            },
            "workspace": {
                "current_dir": current_dir,
                "project_dir": self._project_dir,
                "added_dirs": added_dirs,
            },
            "version": resolve_runtime_version(),
            "output_style": {
                "name": "default",
            },
            "cost": {
                "total_cost_usd": usage.total_cost,
                "total_duration_ms": 0,
                "total_api_duration_ms": 0,
                "total_lines_added": 0,
                "total_lines_removed": 0,
            },
            "context_window": {
                "total_input_tokens": usage.input_tokens,
                "total_output_tokens": usage.output_tokens,
                "context_window_size": max_context,
                "current_usage": {
                    "input_tokens": getattr(usage, "last_input_tokens", 0),
                    "output_tokens": getattr(usage, "last_output_tokens", 0),
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
                "used_percentage": round(used_percentage, 2),
                "remaining_percentage": round(max(0.0, 100.0 - used_percentage), 2),
            },
        }

    def _run_configured_command(self, command: str, payload: dict[str, object]) -> str:
        shell = os.environ.get("SHELL") or "/bin/sh"
        env = os.environ.copy()
        completed = subprocess.run(
            [shell, "-lc", command],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=2,
            env=env,
        )
        if completed.returncode != 0:
            detail = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or f"exit {completed.returncode}"
            )
            return f"statusline error: {detail}"
        return completed.stdout

    def _normalize_command_output(self, output: str) -> str:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        return " | ".join(lines)

    def _render_custom_statusline(self) -> FormattedText | None:
        config = resolve_statusline_config(os.getcwd())
        if config is None:
            return None
        payload = self._build_statusline_payload()
        signature = json.dumps(
            {"command": config.command, "padding": config.padding, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
        )
        if signature != self._cached_command_signature:
            self._cached_command_signature = signature
            self._cached_command_output = self._run_configured_command(config.command, payload)
        normalized = self._normalize_command_output(self._cached_command_output or "")
        if not normalized and not self._notice:
            return None
        fragments: list[tuple[str, str]] = []
        if config.padding:
            fragments.append(("", " " * config.padding))
        if normalized:
            fragments.extend(list(to_formatted_text(ANSI(normalized))))
        if self._notice:
            if normalized:
                fragments.append(("class:status-separator", " | "))
            fragments.extend(
                [
                    ("class:status-label", "Notice: "),
                    ("class:status-value", self._notice),
                ]
            )
        return FormattedText(fragments)

    def get_formatted_text(self) -> FormattedText:
        """Generate the formatted status line text."""
        custom = self._render_custom_statusline()
        if custom is not None:
            return custom

        # Model name - use cached model from usage_tracker to avoid repeated lookups
        model = self.usage_tracker.model
        if "/" in model:
            display_model = model.replace("litellm/", "")
            # If still has provider prefix (e.g., openai/gpt-4o), show just the model
            if "/" in display_model:
                display_model = display_model.split("/")[-1]
        else:
            display_model = model

        # Session: use display name if available, otherwise session ID (truncated)
        session_display = self._display_name or self.session_id

        # Usage data
        usage = self.usage_tracker.session_usage
        cost_str = f"${usage.total_cost:.4f}"

        # Context window usage: current_context_tokens
        # This represents the total context that will be sent in the next turn
        # (assuming sessions automatically include previous conversation history)
        current_tokens = usage.current_context_tokens
        max_context = get_context_window_size(model)
        context_pct = (current_tokens / max_context * 100) if max_context > 0 else 0
        context_style = self._get_context_style(context_pct)

        # Format tokens - show dash before first API call for clearer UX
        if usage.request_count == 0:
            tokens_str = f"–/{self._format_tokens(max_context)}"
        else:
            tokens_str = f"{self._format_tokens(current_tokens)}/{self._format_tokens(max_context)}"

        # PR review status (populated by the background poller).
        pr_fragments: list[tuple[str, str]] = []
        if self.pr_poller is not None:
            pr_status = self.pr_poller.get_status()
            if pr_status is not None:
                style = _PR_STATE_STYLES.get(pr_status.review_state, "class:status-value")
                label = _PR_STATE_LABELS.get(pr_status.review_state, pr_status.review_state)
                pr_fragments = [
                    ("class:status-separator", " | "),
                    (style, f"PR #{pr_status.number} {label}"),
                ]

        columns = self._terminal_columns()
        cwd = os.getcwd()
        fragments: list[tuple[str, str]] = []

        if columns >= 140:
            self._append_field(
                fragments,
                "Model: ",
                self._truncate(display_model, 24),
            )
            self._append_field(fragments, "Dir: ", self._compact_cwd(cwd, 30))
            self._append_field(
                fragments,
                "Session: ",
                self._truncate(session_display, 18, from_start=True),
            )
            self._append_field(fragments, "Tokens: ", tokens_str + " ")
            fragments.append((context_style, f"({context_pct:.1f}%)"))
            self._append_field(fragments, "Cost: ", cost_str)
        elif columns >= 100:
            self._append_field(
                fragments,
                "M: ",
                self._truncate(display_model, 22),
            )
            self._append_field(fragments, "Dir: ", self._compact_cwd(cwd, 28))
            self._append_field(fragments, "Tok: ", tokens_str + " ")
            fragments.append((context_style, f"({context_pct:.1f}%)"))
            self._append_field(fragments, "$: ", cost_str)
        elif columns >= 72:
            self._append_field(
                fragments,
                "M: ",
                self._truncate(display_model, 18),
            )
            self._append_field(fragments, "Dir: ", self._compact_cwd(cwd, 20))
            self._append_field(fragments, "Tok: ", tokens_str)
        else:
            self._append_field(
                fragments,
                "",
                self._truncate(display_model, 14),
            )
            self._append_field(fragments, "", self._compact_cwd(cwd, 14))
            self._append_field(fragments, "", f"{context_pct:.1f}%")

        if pr_fragments:
            rendered_len = sum(len(text) for _, text in fragments)
            pr_len = sum(len(text) for _, text in pr_fragments)
            if rendered_len + pr_len <= columns:
                fragments.extend(pr_fragments)

        if self._notice:
            if self._notice.startswith("Voice error:"):
                notice = self._notice
            else:
                notice = self._truncate(
                    self._notice, max(10, columns - sum(len(t) for _, t in fragments) - 12)
                )
            if notice:
                self._append_field(fragments, "Notice: ", notice)

        return FormattedText(fragments)

    def create_window(self) -> Window:
        """Create a prompt_toolkit Window for the status line."""
        return Window(
            content=FormattedTextControl(self.get_formatted_text),
            height=1,
            dont_extend_height=True,
        )

    def update_session(self, session_id: str) -> None:
        """Update the session ID displayed in the status line."""
        self.session_id = session_id
        self._display_name = None  # Reset display name on session change

    def update_display_name(self, display_name: str) -> None:
        """Update the display name (AI-generated title) for the status line."""
        self._display_name = display_name

    def set_notice(self, notice: Optional[str]) -> None:
        """Set a transient notice shown on the status line."""
        self._notice = notice
