"""Local prompt-backed advisor review command support."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from koder_agent.harness.commands.review_context import (
    collect_local_review_context,
    session_transcript_from_items,
)
from koder_agent.harness.config.schema import RuntimeConfig
from koder_agent.harness.config.service import RuntimeConfigService
from koder_agent.utils.client import llm_completion, resolve_model_override_name

ADVISOR_SYSTEM_PROMPT = """You are a senior engineering advisor reviewing the user's current coding session and pending repository work.

Focus on the highest-signal issues and next actions:
- correctness risks
- regressions introduced by current changes
- incomplete implementation work
- missing or weak tests
- architectural or operational risks that materially affect shipping

Do not spend time on trivial style nits or generic praise.

Return markdown only.
If there are no significant concerns, return:

# Advisor Review

No significant concerns or follow-up recommendations.
"""

ADVISOR_USER_PROMPT_TEMPLATE = """FOCUS:
{focus}

CURRENT MODEL:
{current_model}

ADVISOR MODEL:
{advisor_model}

SESSION TRANSCRIPT:

```
{session_transcript}
```

BASE RANGE:
{base_range}

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

Review the complete local context above.

Required output format:
- Markdown only
- Start with `# Advisor Review`
- Prefer sections like `## Assessment`, `## Risks`, and `## Recommended Next Steps`
- Be concrete, repo-aware, and action-oriented
"""

DEFAULT_ADVISOR_MODELS: dict[str, str] = {
    "openai": "gpt-5.5",
    "custom": "gpt-5.5",
    "chatgpt": "gpt-5.5",
    "github_copilot": "gpt-5.5",
    "anthropic": "claude-opus-4-6",
    "claude": "claude-opus-4-6",
    "antigravity": "claude-opus-4-6",
    "openrouter": "anthropic/claude-opus-4-6",
}


@dataclass(frozen=True)
class AdvisorReviewContext:
    """Collected local context for an advisor review run."""

    current_model: str
    advisor_model: str
    session_transcript: str
    focus: str | None
    git_context: Any | None


def resolve_advisor_model(config: RuntimeConfig | None = None) -> str:
    """Resolve the configured or provider-default advisor model."""
    runtime_config = config or RuntimeConfigService().load()
    override = (runtime_config.harness.advisor_model or "").strip()
    if override:
        return override

    provider = (runtime_config.model.provider or "").strip().lower()
    advisor_model = DEFAULT_ADVISOR_MODELS.get(provider)
    if advisor_model:
        return advisor_model

    raise RuntimeError(
        f"No default advisor model for provider '{provider}'. Set harness.advisor_model in config."
    )


def collect_advisor_review_context(
    *,
    cwd: Path | None = None,
    session_items: list[dict[str, Any]] | None = None,
    focus: str | None = None,
    config: RuntimeConfig | None = None,
) -> AdvisorReviewContext | None:
    """Collect complete local context for an advisor review."""
    session_transcript, _ = session_transcript_from_items(session_items)
    local_context = collect_local_review_context(cwd=cwd, session_items=session_items)
    git_context = local_context.git_context
    has_session = bool(session_transcript.strip())
    has_git_work = bool(git_context and git_context.diff_content.strip())
    if not has_session and not has_git_work:
        return None

    return AdvisorReviewContext(
        current_model=local_context.current_model,
        advisor_model=resolve_advisor_model(config),
        session_transcript=session_transcript or "No current session transcript.",
        focus=focus,
        git_context=git_context,
    )


def build_advisor_review_messages(context: AdvisorReviewContext) -> list[dict[str, str]]:
    """Build the local prompt-backed advisor review request."""
    git_context = context.git_context
    user_prompt = ADVISOR_USER_PROMPT_TEMPLATE.format(
        focus=context.focus or "No special focus provided.",
        current_model=context.current_model,
        advisor_model=context.advisor_model,
        session_transcript=context.session_transcript,
        base_range=git_context.base_range if git_context else "No git repository detected.",
        git_status=git_context.git_status if git_context else "No git repository detected.",
        files_modified=git_context.files_modified if git_context else "No git repository detected.",
        commits=git_context.commits if git_context else "No git repository detected.",
        diff_content=git_context.diff_content if git_context else "",
    )
    return [
        {"role": "system", "content": ADVISOR_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


async def run_advisor_review(
    *,
    cwd: Path | None = None,
    session_items: list[dict[str, Any]] | None = None,
    focus: str | None = None,
    config: RuntimeConfig | None = None,
) -> str:
    """Run a local advisor review against complete session and git context."""
    try:
        context = collect_advisor_review_context(
            cwd=cwd,
            session_items=session_items,
            focus=focus,
            config=config,
        )
    except RuntimeError as exc:
        return f"advisor: {exc}"

    if context is None:
        return "advisor: no current session or pending changes to review."

    try:
        call_model = resolve_model_override_name(context.advisor_model)
        review = await llm_completion(build_advisor_review_messages(context), model=call_model)
    except Exception as exc:
        return f"advisor unavailable: {exc}"

    review_text = review.strip()
    if review_text:
        return review_text
    return "advisor: model returned an empty review."
