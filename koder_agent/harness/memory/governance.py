"""Deterministic governance checks for durable memory data."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

MemoryPayload = dict[str, str]
SkillPayload = dict[str, str]

MAX_CANDIDATE_FILE_BYTES = 64 * 1024
MAX_CANDIDATE_COUNT = 256
MAX_CANDIDATE_QUEUE_BYTES = 8 * 1024 * 1024
MAX_EXTRACTION_RESPONSE_BYTES = 256 * 1024
MAX_EXTRACTION_INPUT_BYTES = 64 * 1024
MAX_EXTRACTION_CANDIDATES = 50
MAX_MEMORY_CONTENT_CHARS = 16_000
MAX_SKILL_INSTRUCTIONS_CHARS = 32_000
MAX_DESCRIPTION_CHARS = 1_000
MAX_NAME_CHARS = 128
MAX_ERROR_SUMMARY_CHARS = 240
MAX_DISPLAY_CHARS = 240

_MEMORY_TYPES = {"user", "feedback", "project", "reference"}
_SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:live-|test-|proj-)?[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{8,}",
        re.IGNORECASE,
    ),
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def contains_high_confidence_secret(value: str) -> bool:
    """Return whether text contains a deterministic credential signature."""

    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def redact_secrets(value: str) -> str:
    """Redact deterministic credential signatures from display/error text."""

    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _has_forbidden_control(value: str, *, multiline: bool) -> bool:
    for character in value:
        if multiline and character in {"\n", "\t"}:
            continue
        category = unicodedata.category(character)
        if category in {"Cc", "Cf"}:
            return True
    return False


def _validate_string(
    value: object,
    *,
    field: str,
    maximum: int,
    multiline: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"candidate field {field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"candidate field {field} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"candidate field {field} exceeds limit")
    if _has_forbidden_control(normalized, multiline=multiline):
        raise ValueError(f"candidate field {field} contains control characters")
    if contains_high_confidence_secret(normalized):
        raise ValueError("candidate contains a high-confidence secret")
    return normalized


def validate_memory_payload(payload: object) -> MemoryPayload:
    """Validate and normalize the strict factual-memory schema."""

    if not isinstance(payload, dict):
        raise ValueError("memory candidate payload must be an object")
    if set(payload) != {"type", "content", "description"}:
        raise ValueError("memory candidate has malformed fields")
    memory_type = _validate_string(payload["type"], field="type", maximum=32)
    if memory_type not in _MEMORY_TYPES:
        raise ValueError("memory candidate has invalid type")
    return {
        "type": memory_type,
        "content": _validate_string(
            payload["content"],
            field="content",
            maximum=MAX_MEMORY_CONTENT_CHARS,
            multiline=True,
        ),
        "description": _validate_string(
            payload["description"],
            field="description",
            maximum=MAX_DESCRIPTION_CHARS,
        ),
    }


def validate_skill_payload(payload: object) -> SkillPayload:
    """Validate and normalize the strict procedural-skill schema."""

    if not isinstance(payload, dict):
        raise ValueError("skill candidate payload must be an object")
    if set(payload) != {"name", "description", "instructions"}:
        raise ValueError("skill candidate has malformed fields")
    return {
        "name": _validate_string(payload["name"], field="name", maximum=MAX_NAME_CHARS),
        "description": _validate_string(
            payload["description"],
            field="description",
            maximum=MAX_DESCRIPTION_CHARS,
        ),
        "instructions": _validate_string(
            payload["instructions"],
            field="instructions",
            maximum=MAX_SKILL_INSTRUCTIONS_CHARS,
            multiline=True,
        ),
    }


def validate_candidate_payload(
    kind: Literal["memory", "skill"], payload: object
) -> MemoryPayload | SkillPayload:
    """Validate a candidate payload for its durable store kind."""

    if kind == "memory":
        return validate_memory_payload(payload)
    return validate_skill_payload(payload)


def sanitize_text(value: object, *, limit: int = MAX_DISPLAY_CHARS) -> str:
    """Redact, flatten controls, and bound untrusted text for output."""

    text = _ANSI_ESCAPE_RE.sub(" ", redact_secrets(str(value)))
    cleaned = []
    for character in text:
        if character.isspace() or unicodedata.category(character) in {"Cc", "Cf"}:
            cleaned.append(" ")
        else:
            cleaned.append(character)
    compact = " ".join("".join(cleaned).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def sanitized_error(
    error: BaseException | str,
    *,
    code: str = "provider_error",
) -> str:
    """Return a classified, redacted, bounded error suitable for persistence."""

    if isinstance(error, BaseException):
        summary = f"{type(error).__name__}: {error}"
    else:
        summary = error
    return f"{code}: {sanitize_text(summary, limit=MAX_ERROR_SUMMARY_CHARS)}"
