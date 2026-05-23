"""Tests for API error classification."""

from koder_agent.agentic.api_errors import (
    ApiErrorCategory,
    classify_api_error,
)


def test_rate_limit_429():
    err = classify_api_error(Exception("Rate limit exceeded"), status_code=429)
    assert err.category == ApiErrorCategory.RATE_LIMIT
    assert "rate limit" in err.user_message.lower()
    assert err.should_retry


def test_rate_limit_529():
    err = classify_api_error(Exception("API overloaded"), status_code=529)
    assert err.category == ApiErrorCategory.OVERLOADED
    assert err.should_retry


def test_auth_error_401():
    err = classify_api_error(Exception("Unauthorized"), status_code=401)
    assert err.category == ApiErrorCategory.AUTH
    assert "api key" in err.user_message.lower() or "auth" in err.user_message.lower()
    assert not err.should_retry


def test_auth_error_403():
    err = classify_api_error(Exception("Forbidden"), status_code=403)
    assert err.category == ApiErrorCategory.AUTH
    assert not err.should_retry


def test_github_copilot_refresh_auth_error():
    err = classify_api_error(
        Exception(
            "litellm.BadRequestError: GetLLMProvider Exception - "
            "litellm.AuthenticationError: Failed to refresh API key: "
            "Failed to refresh API key after maximum retries\n\n"
            "original model: github_copilot/claude-sonnet-4.6"
        )
    )
    assert err.category == ApiErrorCategory.GITHUB_COPILOT_AUTH
    assert "GitHub Copilot" in err.user_message
    assert "koder auth login github_copilot" in err.user_message
    assert not err.should_retry


def test_prompt_too_long():
    err = classify_api_error(
        Exception(
            "This model's maximum context length is 200000 tokens. However, your messages resulted in 250000 tokens"
        ),
        status_code=400,
    )
    assert err.category == ApiErrorCategory.CONTEXT_OVERFLOW
    assert "context" in err.user_message.lower() or "compact" in err.user_message.lower()


def test_invalid_api_key():
    err = classify_api_error(Exception("Invalid API key provided"), status_code=401)
    assert err.category == ApiErrorCategory.AUTH


def test_timeout():
    err = classify_api_error(TimeoutError("Request timed out"), status_code=None)
    assert err.category == ApiErrorCategory.TIMEOUT
    assert err.should_retry


def test_connection_error():
    err = classify_api_error(ConnectionError("Connection refused"), status_code=None)
    assert err.category == ApiErrorCategory.CONNECTION
    assert err.should_retry


def test_server_error_500():
    err = classify_api_error(Exception("Internal server error"), status_code=500)
    assert err.category == ApiErrorCategory.SERVER
    assert err.should_retry


def test_unknown_error():
    err = classify_api_error(Exception("Something weird happened"), status_code=None)
    assert err.category == ApiErrorCategory.UNKNOWN
    assert err.user_message  # Should still have a message


def test_model_not_found():
    err = classify_api_error(
        Exception("The model `nonexistent-model` does not exist"),
        status_code=404,
    )
    assert err.category == ApiErrorCategory.MODEL_NOT_FOUND


def test_content_filter():
    err = classify_api_error(
        Exception("content_policy_violation: Your request was rejected"),
        status_code=400,
    )
    assert err.category == ApiErrorCategory.CONTENT_FILTER
    assert not err.should_retry
