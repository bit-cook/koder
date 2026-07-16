from pathlib import Path

import pytest

from koder_agent.harness.commands import security_review
from koder_agent.utils.client import (
    CompletionTruncationMetadata,
    LLMCompletionResult,
)


@pytest.mark.asyncio
async def test_security_review_reports_truncated_input(monkeypatch):
    context = security_review.SecurityReviewContext(
        repo_root=Path("/tmp/repo"),
        base_range="main...HEAD",
        git_status="M app.py",
        files_modified="app.py",
        commits="abc fix",
        diff_content="+ vulnerable change",
    )
    monkeypatch.setattr(security_review, "collect_security_review_context", lambda _cwd: context)

    async def fake_completion(messages, **kwargs):
        assert kwargs["overflow_policy"] == "truncate"
        assert kwargs["return_metadata"] is True
        return LLMCompletionResult(
            text="# Security Review\n\nPartial result.",
            truncation=CompletionTruncationMetadata(
                model="test",
                context_window=100,
                response_reserve=20,
                original_input_tokens=150,
                sent_input_tokens=80,
            ),
        )

    monkeypatch.setattr(security_review, "llm_completion", fake_completion)

    result = await security_review.run_security_review()

    assert "security-review input warning" in result
    assert "150 -> 80 tokens" in result
