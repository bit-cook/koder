"""Contextual tips system for feature discoverability."""

from collections import deque
from dataclasses import dataclass
from typing import Callable


@dataclass
class TIP:
    """A single tip with optional relevance check."""

    id: str
    message: str
    relevance_check: Callable[[dict], bool] | None = None


# Collection of tips covering key Koder features
TIPS = [
    TIP(
        id="compact_mode",
        message="💡 Tip: Use /compact command to toggle condensed output mode for faster scanning.",
    ),
    TIP(
        id="vim_mode",
        message="💡 Tip: Enable vim keybindings with /vim for efficient terminal navigation.",
        relevance_check=lambda ctx: not ctx.get("in_vim_mode", False),
    ),
    TIP(
        id="tab_completion",
        message="💡 Tip: Press Tab for command completion and file path suggestions.",
    ),
    TIP(
        id="file_mentions",
        message="💡 Tip: Use @filename to reference files in your prompts for better context.",
    ),
    TIP(
        id="voice_mode",
        message="💡 Tip: Try /voice to enable voice input for hands-free coding.",
    ),
    TIP(
        id="skills_system",
        message="💡 Tip: Explore available skills with /skills list - they add specialized capabilities.",
    ),
    TIP(
        id="task_delegate",
        message="💡 Tip: Use task delegation to run parallel investigations and keep your main context clean.",
    ),
    TIP(
        id="git_workflow",
        message="💡 Tip: Koder can help with git operations - just ask to commit, create PRs, or review changes.",
    ),
    TIP(
        id="session_resume",
        message="💡 Tip: Use --resume flag to continue your previous session seamlessly.",
    ),
    TIP(
        id="named_sessions",
        message="💡 Tip: Create named sessions with -s/--session for organizing different workstreams.",
    ),
    TIP(
        id="model_switch",
        message="💡 Tip: Switch models mid-session with /model to leverage different capabilities.",
    ),
    TIP(
        id="shell_background",
        message="💡 Tip: Long-running shell commands can run in background - Koder will notify when done.",
    ),
    TIP(
        id="mcp_servers",
        message="💡 Tip: Connect MCP servers with 'koder mcp add' to extend Koder with custom tools.",
    ),
    TIP(
        id="search_tools",
        message="💡 Tip: Use grep and glob searches to quickly find code patterns across your codebase.",
    ),
    TIP(
        id="approval_mode",
        message="💡 Tip: Toggle approval mode to review commands before execution for safety.",
    ),
    TIP(
        id="reasoning_effort",
        message="💡 Tip: For o1/o3 models, set KODER_REASONING_EFFORT (low/medium/high) to control thinking depth.",
        relevance_check=lambda ctx: ctx.get("model", "").startswith(("o1", "o3", "gpt-5")),
    ),
    TIP(
        id="multi_provider",
        message="💡 Tip: Koder supports multiple AI providers - set KODER_MODEL to switch between OpenAI, Anthropic, Google, and more.",
    ),
    # --- Workflow tips ---
    TIP(
        id="plan_mode",
        message="Tip: Use /plan to enter plan mode — great for designing before implementing.",
    ),
    TIP(
        id="rewind",
        message="Tip: Made a mistake? Use /rewind to roll back conversation to a previous state.",
    ),
    TIP(
        id="diff_review",
        message="Tip: Use /diff to see all uncommitted changes before committing.",
    ),
    TIP(
        id="compact",
        message="Tip: Running low on context? Use /compact to summarize and free up space.",
    ),
    TIP(
        id="export_session",
        message="Tip: Export your conversation with /export for documentation or sharing.",
    ),
    TIP(
        id="copy_response",
        message="Tip: Use /copy to copy the last response to clipboard.",
    ),
    # --- Agent tips ---
    TIP(
        id="agent_delegation",
        message="Tip: Delegate complex sub-tasks to agents — they run in parallel with isolated context.",
    ),
    TIP(
        id="agent_types",
        message="Tip: Try 'Explore' agents for fast codebase search and 'Plan' agents for architecture design.",
    ),
    TIP(
        id="agent_teams",
        message="Tip: Create agent teams for large tasks — teammates coordinate through a shared mailbox.",
    ),
    # --- Plugin tips ---
    TIP(
        id="plugin_install",
        message="Tip: Install plugins with /plugin install <name> to extend Koder with new capabilities.",
    ),
    TIP(
        id="plugin_marketplace",
        message="Tip: Browse available plugins with /plugin search — community plugins add specialized tools.",
    ),
    # --- Hook tips ---
    TIP(
        id="hooks_precommit",
        message="Tip: Set up PreToolUse hooks to auto-validate commands before execution.",
    ),
    TIP(
        id="hooks_notification",
        message="Tip: Configure PostToolUse hooks for notifications when long operations complete.",
    ),
    # --- Git tips ---
    TIP(
        id="git_commit",
        message="Tip: Ask Koder to commit — it reads diffs, crafts messages, and follows your repo's conventions.",
    ),
    TIP(
        id="git_pr",
        message="Tip: Use /commit-push-pr for a complete commit → push → PR workflow in one command.",
    ),
    TIP(
        id="worktree",
        message="Tip: Use worktrees for isolated feature work — changes don't affect your main checkout.",
    ),
    # --- Context tips ---
    TIP(
        id="agents_md",
        message="Tip: Create an AGENTS.md in your project root to give Koder project-specific instructions.",
    ),
    TIP(
        id="agents_md_include",
        message="Tip: Use @path in AGENTS.md to include shared instructions from other files.",
    ),
    TIP(
        id="add_dir",
        message="Tip: Working across repos? Use /add-dir to add additional directories to the workspace.",
    ),
    # --- Security tips ---
    TIP(
        id="permission_modes",
        message="Tip: Use --permission-mode to control tool approval: 'plan' for read-only, 'default' for interactive.",
    ),
    TIP(
        id="sandbox",
        message="Tip: Enable sandbox mode with /sandbox for isolated command execution.",
    ),
    # --- Performance tips ---
    TIP(
        id="context_usage",
        message="Tip: Check context window usage with /context — helps you know when to /compact.",
    ),
    TIP(
        id="cost_tracking",
        message="Tip: Track session costs with /cost — useful for budgeting API usage.",
    ),
    # --- Advanced tips ---
    TIP(
        id="buddy_companion",
        message="Tip: Hatch a coding companion with /buddy hatch — it reacts to your work!",
    ),
    TIP(
        id="cron_scheduling",
        message="Tip: Schedule recurring tasks with cron tools — automate routine checks.",
    ),
    TIP(
        id="env_management",
        message="Tip: Use /env to manage session environment variables without leaving Koder.",
    ),
]


class TipManager:
    """Manages tip rotation with cooldown to prevent repetition."""

    def __init__(self, cooldown_window: int = 10):
        """
        Initialize tip manager.

        Args:
            cooldown_window: Number of recent tips to track for cooldown.
        """
        self._cooldown_window = cooldown_window
        self._shown_history: deque[str] = deque(maxlen=cooldown_window)
        self._tip_index = 0

    def get_tip(self, context: dict | None = None) -> str | None:
        """
        Get a relevant tip that hasn't been shown recently.

        Args:
            context: Optional context dict for relevance checking.

        Returns:
            A tip message or None if all tips are in cooldown.
        """
        context = context or {}

        # Try to find a tip that passes relevance check and isn't in cooldown
        attempts = 0
        max_attempts = len(TIPS) * 2  # Prevent infinite loop

        while attempts < max_attempts:
            tip = TIPS[self._tip_index % len(TIPS)]
            self._tip_index += 1
            attempts += 1

            # Skip if in cooldown
            if tip.id in self._shown_history:
                continue

            # Check relevance if function provided
            if tip.relevance_check is not None:
                if not tip.relevance_check(context):
                    continue

            return tip.message

        # All tips are either in cooldown or filtered by relevance
        return None

    def mark_shown(self, tip_id: str) -> None:
        """
        Mark a tip as shown for cooldown tracking.

        Args:
            tip_id: The ID of the tip that was shown.
        """
        self._shown_history.append(tip_id)
