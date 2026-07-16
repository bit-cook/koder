"""Shared code-review helpers usable from interactive and headless CLI."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

DIFF_TRUNCATE_LIMIT = 15000

REVIEW_SYSTEM_PROMPT = "You are an expert code reviewer. Be concise, specific, and actionable."


@dataclass(frozen=True)
class DiffSelection:
    """The outcome of selecting a diff to review."""

    diff: Optional[str] = None
    context: Optional[str] = None
    error: Optional[str] = None


def select_review_diff(
    *,
    pr: Optional[str] = None,
    base: Optional[str] = None,
    uncommitted: bool = False,
) -> DiffSelection:
    """Select the diff to review from a PR, a base ref, or the working tree.

    Precedence: explicit PR number, then ``--base <ref>`` (``git diff base...HEAD``),
    then ``--uncommitted`` (``git diff HEAD``), then the default fallback which
    reviews uncommitted changes and falls back to staged changes.
    """
    if pr:
        pr_num = pr.lstrip("#")
        try:
            diff_result = subprocess.run(
                ["gh", "pr", "diff", pr_num],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            return DiffSelection(error="gh CLI not found. Install it: https://cli.github.com")
        if diff_result.returncode != 0:
            return DiffSelection(
                error=f"Failed to fetch PR #{pr_num}: {diff_result.stderr.strip()}"
            )
        return DiffSelection(diff=diff_result.stdout, context=f"PR #{pr_num}")

    if base:
        base_ref = base.strip()
        diff_result = subprocess.run(
            ["git", "diff", f"{base_ref}...HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if diff_result.returncode != 0:
            return DiffSelection(
                error=f"Failed to diff against {base_ref}: {diff_result.stderr.strip()}"
            )
        if not diff_result.stdout:
            return DiffSelection(error=f"No changes between {base_ref} and HEAD to review.")
        return DiffSelection(diff=diff_result.stdout, context=f"{base_ref}...HEAD")

    if uncommitted:
        diff_result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not diff_result.stdout:
            return DiffSelection(error="No uncommitted changes to review.")
        return DiffSelection(diff=diff_result.stdout, context="uncommitted changes")

    # Default: uncommitted changes, falling back to staged only.
    diff_result = subprocess.run(
        ["git", "diff", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    diff = diff_result.stdout
    if not diff:
        diff_result = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        diff = diff_result.stdout
    if not diff:
        return DiffSelection(
            error="No changes to review. Make some changes or specify a PR number: /review #123"
        )
    return DiffSelection(diff=diff, context="uncommitted changes")


def build_review_prompt(diff: str, context: str) -> str:
    """Build the LLM review prompt for the given diff and context label."""
    if len(diff) > DIFF_TRUNCATE_LIMIT:
        diff = diff[:DIFF_TRUNCATE_LIMIT] + "\n\n... (diff truncated, showing first 15K chars)"
    return f"""Review the following code changes ({context}).

Focus on:
1. Bugs and logic errors
2. Security vulnerabilities
3. Code quality and readability
4. Missing error handling
5. Test coverage gaps

Be specific — reference file names and line numbers. Prioritize issues by severity.

```diff
{diff}
```"""


async def run_review(
    *,
    pr: Optional[str] = None,
    base: Optional[str] = None,
    uncommitted: bool = False,
) -> tuple[str, bool]:
    """Run a code review against the selected diff.

    Returns a tuple of ``(text, has_findings)`` where ``has_findings`` is True
    when a review actually produced output (used by headless callers to select
    a non-zero exit code). Errors are surfaced as text with ``has_findings``
    False.
    """
    from koder_agent.utils.client import llm_completion

    selection = select_review_diff(pr=pr, base=base, uncommitted=uncommitted)
    if selection.error is not None:
        return selection.error, False

    prompt = build_review_prompt(selection.diff or "", selection.context or "changes")
    try:
        completion = await llm_completion(
            messages=[
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            overflow_policy="truncate",
            return_metadata=True,
        )
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        return f"Review failed: {exc}", False

    if isinstance(completion, str):  # Backwards-compatible test doubles.
        review = completion
        truncation = None
    else:
        review = completion.text
        truncation = completion.truncation
    provenance = ""
    if truncation is not None:
        provenance = (
            "Input warning: the review model received explicitly truncated input "
            f"({truncation.original_input_tokens} -> {truncation.sent_input_tokens} tokens).\n\n"
        )
    text = f"Code Review ({selection.context}):\n\n{provenance}{review}"
    return text, bool(review and review.strip())
