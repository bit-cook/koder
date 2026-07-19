"""Token budgeting helpers for runtime memory flows."""

from __future__ import annotations

import base64
import copy
import json
import math
import struct
from dataclasses import dataclass
from typing import Any

import tiktoken

TRUNCATION_MARKER = "\n\n[truncated to fit context]\n\n"


def _encoder():
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:

        class _FallbackEncoder:
            def encode(self, text: str) -> list[int]:
                return list(text.encode("utf-8"))

        return _FallbackEncoder()


def estimate_text_tokens(text: str) -> int:
    """Estimate token count for plain text."""
    return len(_encoder().encode(text))


def _model_family(model: str | None) -> str:
    value = (model or "").lower()
    if "anthropic" in value or "claude" in value:
        return "anthropic"
    if "gemini" in value or "google" in value or "vertex" in value:
        return "google"
    if "openai" in value or "gpt" in value or "o1" in value or "o3" in value:
        return "openai"
    return "generic"


def _decode_data_url(url: str) -> bytes | None:
    if not url.startswith("data:") or "," not in url:
        return None
    header, payload = url.split(",", 1)
    if ";base64" not in header:
        return None
    try:
        return base64.b64decode(payload, validate=False)
    except Exception:
        return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            return None
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return (width, height) if width and height else None
        index += segment_length
    return None


def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read common raster dimensions without adding an image dependency."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        jpeg = _jpeg_dimensions(data)
        if jpeg is not None:
            return jpeg
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
            kind = data[12:16]
            if kind == b"VP8X":
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return width, height
            if kind == b"VP8L" and data[20] == 0x2F:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except Exception:
        return None
    return None


def estimate_image_tokens(
    image_url: str,
    *,
    detail: str | None = None,
    model: str | None = None,
) -> int:
    """Estimate provider image tokens without tokenizing base64 bytes as text.

    The formulas intentionally follow each provider family's documented shape:
    OpenAI-style tiled accounting, Anthropic's pixel heuristic, and Gemini's
    fixed/tiling accounting. Unknown formats use a conservative bounded fallback.
    """
    data = _decode_data_url(image_url)
    dimensions = _image_dimensions(data) if data else None
    width, height = dimensions or (1024, 1024)
    family = _model_family(model)
    normalized_detail = (detail or "auto").lower()

    if family == "openai":
        if normalized_detail == "low":
            return 85
        scale = min(1.0, 2048 / max(width, height))
        scaled_width = max(1, math.ceil(width * scale))
        scaled_height = max(1, math.ceil(height * scale))
        short_scale = 768 / min(scaled_width, scaled_height)
        if short_scale < 1:
            scaled_width = max(1, math.ceil(scaled_width * short_scale))
            scaled_height = max(1, math.ceil(scaled_height * short_scale))
        tiles = math.ceil(scaled_width / 512) * math.ceil(scaled_height / 512)
        return 85 + 170 * max(1, tiles)
    if family == "anthropic":
        return max(1, math.ceil((width * height) / 750))
    if family == "google":
        tiles = math.ceil(width / 768) * math.ceil(height / 768)
        return 258 * max(1, tiles)

    # Do not let a compressed image's base64 representation dominate the text
    # estimate. The fallback remains conservative and non-zero.
    byte_hint = len(data) if data is not None else 256 * 1024
    return max(256, min(4096, math.ceil(byte_hint / 256)))


def _image_url_from_block(value: dict[str, Any]) -> tuple[str, str | None] | None:
    block_type = value.get("type")
    if block_type not in {"image_url", "input_image"}:
        return None
    raw_url = value.get("image_url")
    if isinstance(raw_url, dict):
        raw_url = raw_url.get("url")
    if not isinstance(raw_url, str):
        return None
    return raw_url, value.get("detail")


def _replace_images_for_estimation(value: Any, *, model: str | None) -> tuple[Any, int]:
    """Return an estimation-only copy plus separately-accounted image tokens."""
    if isinstance(value, dict):
        image = _image_url_from_block(value)
        if image is not None:
            url, detail = image
            replaced = dict(value)
            if isinstance(replaced.get("image_url"), dict):
                replaced["image_url"] = {**replaced["image_url"], "url": "[image]"}
            else:
                replaced["image_url"] = "[image]"
            return replaced, estimate_image_tokens(url, detail=detail, model=model)
        total = 0
        replaced_dict: dict[Any, Any] = {}
        for key, child in value.items():
            replaced_child, child_tokens = _replace_images_for_estimation(child, model=model)
            replaced_dict[key] = replaced_child
            total += child_tokens
        return replaced_dict, total
    if isinstance(value, list):
        total = 0
        replaced_list = []
        for child in value:
            replaced_child, child_tokens = _replace_images_for_estimation(child, model=model)
            replaced_list.append(replaced_child)
            total += child_tokens
        return replaced_list, total
    return value, 0


def estimate_serialized_tokens(value: Any, *, model: str | None = None) -> int:
    """Estimate a complete provider payload, accounting for every item field."""
    try:
        replaced, image_tokens = _replace_images_for_estimation(value, model=model)
        serialized = json.dumps(replaced, sort_keys=True, ensure_ascii=False, default=str)
        return max(1, estimate_text_tokens(serialized) + image_tokens)
    except Exception:
        # Estimation must fail conservative, never as zero. Complete-item
        # serialization retains arguments/output and provider-specific fields.
        try:
            serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            serialized = repr(value)
        return max(1, estimate_text_tokens(serialized))


def estimate_message_tokens(message: dict, *, model: str | None = None) -> int:
    """Estimate token count for one complete transcript/provider item."""
    return estimate_serialized_tokens(message, model=model)


def estimate_messages_tokens(messages: list[dict], *, model: str | None = None) -> int:
    """Estimate token count for a transcript sequence."""
    return sum(estimate_message_tokens(message, model=model) for message in messages)


@dataclass(frozen=True)
class ContextPreflightEstimate:
    """Componentized token estimate for one model call."""

    context_window: int
    response_reserve: int
    static_tokens: int = 0
    tool_tokens: int = 0
    schema_tokens: int = 0
    extra_tokens: int = 0
    history_tokens: int = 0
    input_tokens: int = 0

    @property
    def available_input_tokens(self) -> int:
        """Context available before reserving model output."""
        return max(0, self.context_window - self.response_reserve)

    @property
    def required_without_history(self) -> int:
        """Unrecoverable request cost: static/tool/input plus output reserve."""
        return (
            self.static_tokens
            + self.tool_tokens
            + self.schema_tokens
            + self.extra_tokens
            + self.input_tokens
            + self.response_reserve
        )

    @property
    def required_tokens(self) -> int:
        """Total estimated request plus response reserve."""
        return self.required_without_history + self.history_tokens

    @property
    def overage_tokens(self) -> int:
        """Tokens by which the estimate exceeds the model context window."""
        return max(0, self.required_tokens - self.context_window)

    @property
    def fits(self) -> bool:
        return self.required_tokens <= self.context_window

    @property
    def history_recoverable(self) -> bool:
        """Whether removing history could make the request fit."""
        return not self.fits and self.required_without_history <= self.context_window


def estimate_context_preflight(
    *,
    context_window: int,
    response_reserve: int,
    static_tokens: int = 0,
    tool_tokens: int = 0,
    schema_tokens: int = 0,
    extra_tokens: int = 0,
    history_tokens: int = 0,
    input_tokens: int = 0,
) -> ContextPreflightEstimate:
    """Build a normalized context estimate without double-counting components."""
    return ContextPreflightEstimate(
        context_window=max(1, int(context_window)),
        response_reserve=max(0, int(response_reserve)),
        static_tokens=max(0, int(static_tokens)),
        tool_tokens=max(0, int(tool_tokens)),
        schema_tokens=max(0, int(schema_tokens)),
        extra_tokens=max(0, int(extra_tokens)),
        history_tokens=max(0, int(history_tokens)),
        input_tokens=max(0, int(input_tokens)),
    )


def estimate_model_request_preflight(
    *,
    context_window: int,
    response_reserve: int,
    instructions: Any = None,
    input_items: Any = None,
    tools: Any = None,
    response_format: Any = None,
    extra_payload: Any = None,
    model: str | None = None,
) -> ContextPreflightEstimate:
    """Shared request-budget contract for scheduler, model, and auxiliary calls."""
    return estimate_context_preflight(
        context_window=context_window,
        response_reserve=response_reserve,
        static_tokens=(
            estimate_serialized_tokens(instructions, model=model)
            if instructions not in (None, "")
            else 0
        ),
        tool_tokens=(
            estimate_serialized_tokens(tools, model=model) if tools not in (None, [], {}) else 0
        ),
        schema_tokens=(
            estimate_serialized_tokens(response_format, model=model)
            if response_format not in (None, {}, [])
            else 0
        ),
        extra_tokens=(
            estimate_serialized_tokens(extra_payload, model=model)
            if extra_payload not in (None, {}, [])
            else 0
        ),
        input_tokens=(
            estimate_serialized_tokens(input_items, model=model)
            if input_items not in (None, "", [])
            else 0
        ),
    )


def format_context_preflight_diagnostic(
    estimate: ContextPreflightEstimate,
    *,
    subject: str = "Request",
) -> str:
    """Return a precise user-facing diagnostic for an impossible model call."""
    return (
        f"{subject} cannot fit the model context before a provider call: "
        f"context window={estimate.context_window}, "
        f"instructions={estimate.static_tokens}, tools={estimate.tool_tokens}, "
        f"structured output={estimate.schema_tokens}, extras={estimate.extra_tokens}, "
        f"history={estimate.history_tokens}, current input={estimate.input_tokens}, "
        f"response reserve={estimate.response_reserve}, "
        f"required={estimate.required_tokens}, over by={estimate.overage_tokens}."
    )


class ContextPreflightError(ValueError):
    """Raised when a model call cannot be made within its context window."""

    def __init__(
        self,
        estimate: ContextPreflightEstimate,
        *,
        subject: str = "Request",
    ) -> None:
        self.estimate = estimate
        super().__init__(format_context_preflight_diagnostic(estimate, subject=subject))


def _head_tail_with_marker(text: str, kept_chars: int, marker: str) -> str:
    """Keep one-third of the requested characters from the head and the rest from the tail."""
    head_chars = kept_chars // 3
    tail_chars = kept_chars - head_chars
    tail = text[-tail_chars:] if tail_chars else ""
    return f"{text[:head_chars]}{marker}{tail}"


def truncate_text_to_tokens(
    text: str,
    max_tokens: int,
    *,
    marker: str = TRUNCATION_MARKER,
) -> str:
    """Truncate text to a token budget while preserving both its head and tail."""
    if max_tokens <= 0:
        raise ValueError("Token budget cannot fit a marked truncation")
    if estimate_text_tokens(text) <= max_tokens:
        return text
    if estimate_text_tokens(marker) > max_tokens:
        raise ValueError("Token budget cannot fit the truncation marker")

    low = 0
    high = len(text)
    best = marker
    while low <= high:
        kept_chars = (low + high) // 2
        candidate = _head_tail_with_marker(text, kept_chars, marker)
        if estimate_text_tokens(candidate) <= max_tokens:
            best = candidate
            low = kept_chars + 1
        else:
            high = kept_chars - 1
    return best


def truncate_messages_to_token_budget(
    messages: list[dict],
    max_tokens: int,
) -> list[dict] | None:
    """Fit mutable message content to a budget without dropping fixed messages.

    System/developer messages are considered fixed. String content in other
    messages is truncated largest-first, preserving both the beginning and end.
    ``None`` means the fixed message framing/instructions alone cannot fit.
    """
    fitted = copy.deepcopy(messages)
    if estimate_messages_tokens(fitted) <= max_tokens:
        return fitted

    latest_user_index = next(
        (index for index in range(len(fitted) - 1, -1, -1) if fitted[index].get("role") == "user"),
        None,
    )
    candidates = [
        index
        for index, message in enumerate(fitted)
        if message.get("role") not in {"system", "developer"}
        and isinstance(message.get("content"), str)
    ]
    candidates.sort(
        key=lambda index: (
            index == latest_user_index,
            -estimate_text_tokens(str(fitted[index].get("content", ""))),
        )
    )

    for index in candidates:
        if estimate_messages_tokens(fitted) <= max_tokens:
            break
        original = str(fitted[index].get("content", ""))
        # First make deterministic progress even when several messages are
        # simultaneously oversized. Expanding this message is only possible
        # after the other payload already fits around its mandatory marker.
        fitted[index]["content"] = TRUNCATION_MARKER
        if estimate_messages_tokens(fitted) > max_tokens:
            continue
        low = 0
        high = len(original)
        best = TRUNCATION_MARKER
        while low <= high:
            kept_chars = (low + high) // 2
            candidate = _head_tail_with_marker(original, kept_chars, TRUNCATION_MARKER)
            trial = copy.deepcopy(fitted)
            trial[index]["content"] = candidate
            if estimate_messages_tokens(trial) <= max_tokens:
                best = candidate
                low = kept_chars + 1
            else:
                high = kept_chars - 1
        fitted[index]["content"] = best

    if estimate_messages_tokens(fitted) > max_tokens:
        return None
    return fitted
