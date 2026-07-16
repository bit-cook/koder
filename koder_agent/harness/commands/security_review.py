"""Local prompt-backed security review command support."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from koder_agent.utils.client import llm_completion

SECURITY_REVIEW_SYSTEM_PROMPT = """You are a senior security engineer conducting a focused security review of pending branch changes.

Focus only on high-confidence security vulnerabilities newly introduced by the provided diff.
Do not comment on general code quality, style, low-impact issues, or pre-existing concerns.

Hard exclusions:
- denial of service or resource exhaustion concerns
- secrets stored on disk
- rate limiting concerns
- documentation-only issues
- findings that only affect tests
- theoretical hardening gaps without a concrete exploit path

Return markdown only.
If you do not find a high-confidence issue, return:

# Security Review

No high-confidence security findings.
"""

SECURITY_REVIEW_USER_PROMPT_TEMPLATE = """BASE RANGE: {base_range}

GIT STATUS:

```
{git_status}
```

FILES MODIFIED:

```
{files_modified}
```

COMMITS:

```
{commits}
```

DIFF CONTENT:

```diff
{diff_content}
```

Review the complete diff above. This contains the code changes under review.

Required output format:
- Use markdown only
- For each finding, include file, line number, severity, category, description, exploit scenario, and recommendation
- Focus on HIGH and MEDIUM findings only
- Minimize false positives; only report issues you are highly confident are exploitable
"""


@dataclass(frozen=True)
class SecurityReviewContext:
    """Collected git context for a local security review run."""

    repo_root: Path
    base_range: str
    git_status: str
    files_modified: str
    commits: str
    diff_content: str


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _render_git_output(proc: subprocess.CompletedProcess[str], *, empty: str = "") -> str:
    if proc.returncode == 0:
        return proc.stdout.strip() or empty
    return proc.stderr.strip() or empty


def _resolve_repo_root(cwd: Path) -> Path | None:
    proc = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if proc.returncode != 0:
        return None
    output = proc.stdout.strip()
    return Path(output) if output else None


def _resolve_base_range(repo_root: Path) -> str:
    origin_head = _run_git(["rev-parse", "--verify", "origin/HEAD"], cwd=repo_root)
    if origin_head.returncode == 0:
        return "origin/HEAD..."
    return "HEAD"


def collect_security_review_context(cwd: Path | None = None) -> SecurityReviewContext | None:
    """Collect git status, files, commits, and diff content for review."""

    repo_cwd = (cwd or Path.cwd()).resolve()
    repo_root = _resolve_repo_root(repo_cwd)
    if repo_root is None:
        return None

    base_range = _resolve_base_range(repo_root)
    git_status = _render_git_output(
        _run_git(["status", "--short"], cwd=repo_root),
        empty="Clean working tree.",
    )
    files_modified = _render_git_output(
        _run_git(["diff", "--name-only", base_range], cwd=repo_root),
        empty="No files changed.",
    )
    commits = _render_git_output(
        _run_git(["log", "--no-decorate", "--oneline", base_range], cwd=repo_root),
        empty="No branch commits.",
    )
    diff_content = _render_git_output(
        _run_git(["diff", base_range], cwd=repo_root),
        empty="",
    )
    return SecurityReviewContext(
        repo_root=repo_root,
        base_range=base_range,
        git_status=git_status,
        files_modified=files_modified,
        commits=commits,
        diff_content=diff_content,
    )


def build_security_review_messages(context: SecurityReviewContext) -> list[dict[str, str]]:
    """Build the local prompt-backed review request."""

    user_prompt = SECURITY_REVIEW_USER_PROMPT_TEMPLATE.format(
        base_range=context.base_range,
        git_status=context.git_status,
        files_modified=context.files_modified,
        commits=context.commits,
        diff_content=context.diff_content,
    )
    return [
        {"role": "system", "content": SECURITY_REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


async def run_security_review(*, cwd: Path | None = None) -> str:
    """Run a local security review contract against pending git changes."""

    context = collect_security_review_context(cwd)
    if context is None:
        return "security-review: no git repository detected."
    if not context.diff_content.strip():
        return "security-review: no pending changes to review."

    completion = await llm_completion(
        build_security_review_messages(context),
        overflow_policy="truncate",
        return_metadata=True,
    )
    if isinstance(completion, str):  # Backwards-compatible test doubles.
        review_text = completion.strip()
        truncation = None
    else:
        review_text = completion.text.strip()
        truncation = completion.truncation
    if truncation is not None:
        review_text = (
            "security-review input warning: the model received explicitly truncated input "
            f"({truncation.original_input_tokens} -> {truncation.sent_input_tokens} tokens).\n\n"
            f"{review_text}"
        )
    if review_text:
        return review_text
    return "security-review: model returned an empty review."
