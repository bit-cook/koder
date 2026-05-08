"""Tests for AI-powered shell command classifier."""

from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.harness.permissions.ai_classifier import (
    AiShellClassifier,
    ClassificationResult,
    RiskLevel,
)


def test_risk_levels():
    assert RiskLevel.SAFE.value == "safe"
    assert RiskLevel.MODERATE.value == "moderate"
    assert RiskLevel.DANGEROUS.value == "dangerous"


def test_classification_result():
    r = ClassificationResult(
        command="ls -la",
        risk_level=RiskLevel.SAFE,
        allowed=True,
        reason="Read-only directory listing",
    )
    assert r.allowed
    assert r.risk_level == RiskLevel.SAFE


@pytest.mark.asyncio
async def test_safe_command():
    """LLM should classify read-only commands as safe."""
    mock_response = '{"risk_level": "safe", "allowed": true, "reason": "Read-only listing"}'

    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        classifier = AiShellClassifier()
        result = await classifier.classify("ls -la /tmp")
        assert result.allowed
        assert result.risk_level == RiskLevel.SAFE


@pytest.mark.asyncio
async def test_dangerous_command():
    """LLM should classify destructive commands as dangerous."""
    mock_response = (
        '{"risk_level": "dangerous", "allowed": false, "reason": "Recursive force delete"}'
    )

    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        classifier = AiShellClassifier()
        result = await classifier.classify("rm -rf /")
        assert not result.allowed
        assert result.risk_level == RiskLevel.DANGEROUS


@pytest.mark.asyncio
async def test_moderate_command():
    """LLM should flag uncertain commands as moderate."""
    mock_response = (
        '{"risk_level": "moderate", "allowed": true, "reason": "Git operation, generally safe"}'
    )

    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        classifier = AiShellClassifier()
        result = await classifier.classify("git push origin main")
        assert result.risk_level == RiskLevel.MODERATE


@pytest.mark.asyncio
async def test_fallback_on_llm_error():
    """Should fall back to 'ask user' on LLM failure."""
    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        side_effect=Exception("API error"),
    ):
        classifier = AiShellClassifier()
        result = await classifier.classify("some command")
        assert result.risk_level == RiskLevel.MODERATE
        assert not result.allowed  # Default to deny on error


@pytest.mark.asyncio
async def test_malformed_response():
    """Should handle malformed LLM response."""
    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        return_value="not json at all",
    ):
        classifier = AiShellClassifier()
        result = await classifier.classify("echo hello")
        assert result.risk_level == RiskLevel.MODERATE


def test_classification_prompt_exists():
    classifier = AiShellClassifier()
    assert "safe" in classifier.system_prompt.lower()
    assert "dangerous" in classifier.system_prompt.lower()


@pytest.mark.asyncio
async def test_custom_context():
    """Should include custom context in the prompt."""
    mock_response = '{"risk_level": "safe", "allowed": true, "reason": "ok"}'

    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_llm:
        classifier = AiShellClassifier()
        await classifier.classify("make build", context="This is a Go project")
        # Verify context was included in the prompt
        call_args = mock_llm.call_args
        messages = (
            call_args[1].get("messages") or call_args[0][0]
            if call_args[0]
            else call_args[1]["messages"]
        )
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "Go project" in user_msg["content"]
