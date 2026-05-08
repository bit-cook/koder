"""Model deprecation warnings for Koder.

This module tracks model retirement dates and provides warnings when users
attempt to use deprecated or soon-to-be-deprecated models.
"""

from datetime import datetime

# Deprecation schedule mapping model names to retirement dates
DEPRECATION_SCHEDULE = {
    # Claude 3 Opus - deprecated
    "claude-3-opus-20240229": "2025-03-01",
    # Claude 3 Haiku - deprecated
    "claude-3-haiku-20240307": "2025-03-01",
    # Claude 3.5 Haiku - deprecated
    "claude-3-5-haiku-20241022": "2025-04-01",
    # Claude 3.5 Sonnet v1 - deprecated
    "claude-3-5-sonnet-20240620": "2025-04-01",
    # Claude 3.5 Sonnet v2 - deprecated
    "claude-3-5-sonnet-20241022": "2025-06-01",
    # Claude 3.7 Sonnet - deprecated
    "claude-3-7-sonnet-20250219": "2025-05-01",
    # GPT-4 base - deprecated
    "gpt-4-0314": "2025-06-01",
    "gpt-4-0613": "2025-06-01",
    # GPT-4 Turbo previews - deprecated
    "gpt-4-1106-preview": "2025-04-01",
    "gpt-4-0125-preview": "2025-04-01",
}


def check_model_deprecation(model: str) -> str | None:
    """Check if a model is deprecated or retiring soon.

    Args:
        model: The model name to check.

    Returns:
        A warning message if the model is deprecated or retiring within 30 days,
        None otherwise.
    """
    if model not in DEPRECATION_SCHEDULE:
        return None

    retirement_date_str = DEPRECATION_SCHEDULE[model]
    retirement_date = datetime.strptime(retirement_date_str, "%Y-%m-%d")
    now = datetime.now()

    # Check if model is already retired
    if retirement_date <= now:
        return (
            f"WARNING: Model '{model}' was deprecated and retired on {retirement_date_str}. "
            "Please switch to a supported model."
        )

    # Check if model is retiring within 30 days
    days_until_retirement = (retirement_date - now).days
    if days_until_retirement <= 30:
        return (
            f"WARNING: Model '{model}' is retiring in {days_until_retirement} days "
            f"(on {retirement_date_str}). Please plan to switch to a supported model."
        )

    return None
