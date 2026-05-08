"""API error classification with user-friendly messages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ApiErrorCategory(Enum):
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    AUTH = "auth"
    CONTEXT_OVERFLOW = "context_overflow"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER = "server"
    MODEL_NOT_FOUND = "model_not_found"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


# Categories that should trigger retry
_RETRYABLE = {
    ApiErrorCategory.RATE_LIMIT,
    ApiErrorCategory.OVERLOADED,
    ApiErrorCategory.TIMEOUT,
    ApiErrorCategory.CONNECTION,
    ApiErrorCategory.SERVER,
}

_USER_MESSAGES = {
    ApiErrorCategory.RATE_LIMIT: "Rate limit exceeded. Waiting before retrying...",
    ApiErrorCategory.OVERLOADED: "API is overloaded (529). Waiting before retrying...",
    ApiErrorCategory.AUTH: "Authentication failed. Check your API key (KODER_API_KEY or provider-specific key).",
    ApiErrorCategory.CONTEXT_OVERFLOW: "Context window exceeded. Try /compact to reduce conversation size.",
    ApiErrorCategory.TIMEOUT: "Request timed out. Retrying...",
    ApiErrorCategory.CONNECTION: "Connection failed. Check your network and API endpoint.",
    ApiErrorCategory.SERVER: "Server error. Retrying...",
    ApiErrorCategory.MODEL_NOT_FOUND: "Model not found. Check your KODER_MODEL setting. Use 'sonnet', 'opus', or 'haiku' for Claude models.",
    ApiErrorCategory.CONTENT_FILTER: "Request rejected by content filter.",
    ApiErrorCategory.UNKNOWN: "An unexpected error occurred.",
}


@dataclass(frozen=True)
class ClassifiedError:
    category: ApiErrorCategory
    user_message: str
    original_error: Exception
    should_retry: bool
    status_code: int | None = None


def classify_api_error(
    error: Exception,
    status_code: int | None = None,
) -> ClassifiedError:
    """Classify an API error and return a user-friendly message."""
    msg = str(error).lower()

    # Determine category from status code first
    if status_code == 429:
        category = ApiErrorCategory.RATE_LIMIT
    elif status_code == 529:
        category = ApiErrorCategory.OVERLOADED
    elif status_code in (401, 403):
        category = ApiErrorCategory.AUTH
    elif status_code == 404 and ("model" in msg or "does not exist" in msg):
        category = ApiErrorCategory.MODEL_NOT_FOUND
    elif status_code == 404:
        category = ApiErrorCategory.MODEL_NOT_FOUND
    elif status_code and 500 <= status_code < 600:
        category = ApiErrorCategory.SERVER
    # Then check error message patterns
    elif isinstance(error, TimeoutError) or "timeout" in msg or "timed out" in msg:
        category = ApiErrorCategory.TIMEOUT
    elif isinstance(error, ConnectionError) or "connection" in msg:
        category = ApiErrorCategory.CONNECTION
    elif "rate limit" in msg or "rate_limit" in msg:
        category = ApiErrorCategory.RATE_LIMIT
    elif "context length" in msg or "maximum.*tokens" in msg or "too many tokens" in msg:
        category = ApiErrorCategory.CONTEXT_OVERFLOW
    elif "api key" in msg or "unauthorized" in msg or "invalid.*key" in msg:
        category = ApiErrorCategory.AUTH
    elif "content_policy" in msg or "content filter" in msg or "safety" in msg:
        category = ApiErrorCategory.CONTENT_FILTER
    elif "overloaded" in msg:
        category = ApiErrorCategory.OVERLOADED
    elif status_code == 400 and ("context" in msg or "token" in msg):
        category = ApiErrorCategory.CONTEXT_OVERFLOW
    else:
        category = ApiErrorCategory.UNKNOWN

    return ClassifiedError(
        category=category,
        user_message=_USER_MESSAGES[category],
        original_error=error,
        should_retry=category in _RETRYABLE,
        status_code=status_code,
    )
