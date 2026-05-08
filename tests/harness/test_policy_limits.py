from __future__ import annotations

from types import SimpleNamespace

from koder_agent.harness.policy_limits import (
    classify_policy_limit,
    render_policy_limit_options,
)


def test_render_policy_limit_options_uses_runtime_counters():
    config = SimpleNamespace(model=SimpleNamespace(provider="openai", name="gpt-test"))
    usage = SimpleNamespace(request_count=3, input_tokens=1200, output_tokens=345)
    scheduler = SimpleNamespace(usage_tracker=SimpleNamespace(session_usage=usage))

    output = render_policy_limit_options(scheduler=scheduler, config=config)

    assert "rate-limit-options:" in output
    assert "provider: openai" in output
    assert "model: gpt-test" in output
    assert "requests: 3" in output
    assert "input_tokens: 1200" in output
    assert "output_tokens: 345" in output
    assert "retryable_errors: rate_limit, overloaded, timeout, connection, server" in output
    assert "/compact to reduce context before retrying" in output


def test_classify_policy_limit_wraps_retryable_errors():
    decision = classify_policy_limit(Exception("rate limit exceeded"), status_code=429)

    assert decision.category == "rate_limit"
    assert decision.should_retry is True
    assert "Rate limit" in decision.message
    assert "/usage to inspect current counters" in decision.local_options
