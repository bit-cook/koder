"""Bundled skills that ship with koder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from koder_agent.tools.skill import Skill


@dataclass(frozen=True)
class BundledSkillDefinition:
    name: str
    description: str
    content: str
    argument_hint: str | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    execution_context: str | None = None
    agent: str | None = None


def _definitions() -> list[BundledSkillDefinition]:
    return [
        BundledSkillDefinition(
            name="batch",
            description="Research and plan a large-scale change, then execute it in parallel across isolated worktree agents.",
            argument_hint="<instruction>",
            disable_model_invocation=True,
            content=(
                "Plan a large batch change for $ARGUMENTS.\n\n"
                "1. Research the codebase and break the work into independent units.\n"
                "2. Require one worktree-isolated background subagent per unit.\n"
                "3. Give each worker a concrete verification recipe.\n"
                "4. Track PR/status output for each worker."
            ),
        ),
        BundledSkillDefinition(
            name="claude-api",
            description="Load Claude API and SDK guidance for the current language or use case.",
            content=(
                "Help with Claude API or SDK usage for $ARGUMENTS.\n\n"
                "Focus on the current project language, tool use, structured output, streaming, "
                "batches, and common pitfalls."
            ),
        ),
        BundledSkillDefinition(
            name="debug",
            description="Enable debug logging for the session and diagnose issues from the debug log.",
            argument_hint="[issue description]",
            disable_model_invocation=True,
            content=(
                "Debug the current koder session for $ARGUMENTS.\n\n"
                "1. Inspect the relevant debug and runtime state.\n"
                "2. Summarize errors or warnings.\n"
                "3. Suggest concrete next steps."
            ),
        ),
        BundledSkillDefinition(
            name="loop",
            description="Run a prompt or slash command on a recurring interval.",
            argument_hint="[interval] <prompt>",
            content=(
                "Schedule a recurring action for $ARGUMENTS.\n\n"
                "Parse the interval, confirm the cadence, and then execute the prompt once immediately."
            ),
        ),
        BundledSkillDefinition(
            name="simplify",
            description="Review changed code for reuse, quality, and efficiency, then fix issues found.",
            content=(
                "Review the changed code and simplify it.\n\n"
                "1. Look for reuse opportunities.\n"
                "2. Look for quality issues.\n"
                "3. Look for efficiency problems.\n"
                "4. Apply fixes and summarize the cleanup."
            ),
        ),
        BundledSkillDefinition(
            name="remember",
            description="Save important context to persistent memory for future sessions.",
            argument_hint="<what to remember>",
            content=(
                "Save the following to persistent memory: $ARGUMENTS\n\n"
                "1. Determine the memory type (user, feedback, project, or reference).\n"
                "2. Write a memory file to the project's .koder/memory/ directory.\n"
                "3. Update MEMORY.md index with a one-line entry.\n"
                "4. Confirm what was saved and why it will be useful."
            ),
        ),
        BundledSkillDefinition(
            name="stuck",
            description="Diagnose why you're stuck and try alternative approaches.",
            content=(
                "You appear to be stuck. Diagnose and recover.\n\n"
                "1. Identify what you were trying to do and what went wrong.\n"
                "2. List 3 alternative approaches you haven't tried.\n"
                "3. Pick the most promising alternative and explain why.\n"
                "4. Execute that approach immediately.\n"
                "5. If still stuck after the alternative, escalate to the user with a clear description of the blocker."
            ),
        ),
        BundledSkillDefinition(
            name="verify",
            description="Verify that recent changes work correctly before declaring done.",
            content=(
                "Verify the recent changes are correct.\n\n"
                "1. Identify what was just changed (check git diff or recent tool calls).\n"
                "2. Run relevant tests (find test files, run them).\n"
                "3. Check for lint/type errors (run the project's lint command).\n"
                "4. Verify the change actually solves the original request.\n"
                "5. Report: what was verified, what passed, what failed."
            ),
        ),
        BundledSkillDefinition(
            name="update-config",
            description="Update koder settings and configuration via settings.json.",
            argument_hint="<setting to change>",
            content=(
                "Update koder configuration for $ARGUMENTS.\n\n"
                "1. Read the current settings from .koder/settings.json and ~/.koder/settings.json.\n"
                "2. Identify the setting to change.\n"
                "3. Apply the change to the appropriate scope (project or user).\n"
                "4. Confirm what was changed and where."
            ),
        ),
    ]


def get_bundled_skills() -> dict[str, Skill]:
    from koder_agent.tools.skill import Skill

    bundled: dict[str, Skill] = {}
    for definition in _definitions():
        bundled[definition.name] = Skill(
            name=definition.name,
            description=definition.description,
            content=definition.content,
            source="bundled",
            disable_model_invocation=definition.disable_model_invocation,
            user_invocable=definition.user_invocable,
            argument_hint=definition.argument_hint,
            execution_context=definition.execution_context,
            agent=definition.agent,
            base_dir=Path("<bundled>"),
        )
    return bundled
