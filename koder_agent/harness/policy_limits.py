"""Local provider-limit status helpers for Koder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from koder_agent.agentic.api_errors import ApiErrorCategory, classify_api_error
from koder_agent.config import get_config

RETRYABLE_POLICY_LIMIT_ERRORS = (
    ApiErrorCategory.RATE_LIMIT,
    ApiErrorCategory.OVERLOADED,
    ApiErrorCategory.TIMEOUT,
    ApiErrorCategory.CONNECTION,
    ApiErrorCategory.SERVER,
)

LOCAL_LIMIT_OPTIONS = (
    "/usage to inspect current counters",
    "/compact to reduce context before retrying",
    "/model <model> to switch model labels for the session",
    "/effort <low|medium|high|auto> to tune reasoning cost",
    "KODER_BASE_URL can route to a different compatible endpoint",
)


@dataclass(frozen=True)
class PolicyLimitSnapshot:
    provider: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class PolicyLimitDecision:
    category: str
    should_retry: bool
    message: str
    local_options: tuple[str, ...]


def build_policy_limit_snapshot(scheduler: Any = None, config: Any = None) -> PolicyLimitSnapshot:
    config = config or get_config()
    usage = scheduler.usage_tracker.session_usage if scheduler else None
    return PolicyLimitSnapshot(
        provider=str(getattr(config.model, "provider", "unknown") or "unknown"),
        model=str(getattr(config.model, "name", "unknown") or "unknown"),
        requests=int(getattr(usage, "request_count", 0) if usage else 0),
        input_tokens=int(getattr(usage, "input_tokens", 0) if usage else 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) if usage else 0),
    )


def classify_policy_limit(error: Exception, status_code: int | None = None) -> PolicyLimitDecision:
    classified = classify_api_error(error, status_code=status_code)
    return PolicyLimitDecision(
        category=classified.category.value,
        should_retry=classified.should_retry,
        message=classified.user_message,
        local_options=LOCAL_LIMIT_OPTIONS,
    )


def render_policy_limit_options(scheduler: Any = None, config: Any = None) -> str:
    snapshot = build_policy_limit_snapshot(scheduler=scheduler, config=config)
    retryable = ", ".join(category.value for category in RETRYABLE_POLICY_LIMIT_ERRORS)
    options = "\n".join(f"- {option}" for option in LOCAL_LIMIT_OPTIONS)
    return (
        "rate-limit-options:\n"
        f"provider: {snapshot.provider}\n"
        f"model: {snapshot.model}\n"
        f"requests: {snapshot.requests}\n"
        f"input_tokens: {snapshot.input_tokens}\n"
        f"output_tokens: {snapshot.output_tokens}\n"
        f"retryable_errors: {retryable}\n"
        "local_options:\n"
        f"{options}"
    )
