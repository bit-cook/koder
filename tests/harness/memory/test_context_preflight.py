"""Tests for shared model-call context preflight estimation."""

import base64
import copy
import struct

from koder_agent.harness.memory.budget import (
    TRUNCATION_MARKER,
    estimate_context_preflight,
    estimate_message_tokens,
    estimate_serialized_tokens,
    truncate_messages_to_token_budget,
)


def test_preflight_counts_static_tools_history_input_and_reserve_once():
    estimate = estimate_context_preflight(
        context_window=100,
        response_reserve=20,
        static_tokens=10,
        tool_tokens=15,
        history_tokens=25,
        input_tokens=30,
    )

    assert estimate.required_tokens == 100
    assert estimate.required_without_history == 75
    assert estimate.available_input_tokens == 80
    assert estimate.fits is True
    assert estimate.history_recoverable is False


def test_model_preflight_counts_structured_output_schema():
    from koder_agent.harness.memory.budget import estimate_model_request_preflight

    estimate = estimate_model_request_preflight(
        context_window=200,
        response_reserve=20,
        instructions="system",
        input_items=[{"role": "user", "content": "json please"}],
        tools=[{"type": "function", "name": "lookup", "parameters": {}}],
        response_format={
            "format": {
                "type": "json_schema",
                "schema": {"description": "s" * 20_000},
            }
        },
        model="gpt-4.1",
    )

    assert estimate.schema_tokens > 200
    assert estimate.fits is False


def test_preflight_boundary_includes_response_reserve():
    exact = estimate_context_preflight(
        context_window=64,
        response_reserve=16,
        static_tokens=8,
        tool_tokens=8,
        history_tokens=16,
        input_tokens=16,
    )
    over = estimate_context_preflight(
        context_window=64,
        response_reserve=16,
        static_tokens=8,
        tool_tokens=8,
        history_tokens=16,
        input_tokens=17,
    )

    assert exact.fits is True
    assert exact.overage_tokens == 0
    assert over.fits is False
    assert over.overage_tokens == 1
    assert over.history_recoverable is True


def test_preflight_marks_input_and_static_overhead_as_impossible():
    estimate = estimate_context_preflight(
        context_window=100,
        response_reserve=20,
        static_tokens=30,
        tool_tokens=20,
        history_tokens=10,
        input_tokens=40,
    )

    assert estimate.fits is False
    assert estimate.required_without_history == 110
    assert estimate.history_recoverable is False


def test_tiny_budget_fails_instead_of_blank_user_message():
    messages = [{"role": "user", "content": "critical current intent"}]
    fitted = truncate_messages_to_token_budget(messages, max_tokens=1)

    assert fitted is None
    assert messages[0]["content"] == "critical current intent"


def test_latest_user_message_is_not_silently_truncated():
    messages = [
        {"role": "user", "content": "old context " * 500},
        {"role": "assistant", "content": "old answer " * 500},
        {"role": "user", "content": "latest exact request"},
    ]
    latest = messages[-1]["content"]

    fitted = truncate_messages_to_token_budget(messages, max_tokens=180)

    assert fitted is not None
    assert fitted[-1]["content"] == latest
    assert any(TRUNCATION_MARKER.strip() in item["content"] for item in fitted[:-1])


def test_estimator_error_falls_back_to_complete_item_serialization(monkeypatch):
    item = {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": "evidence " * 1_000,
    }

    import koder_agent.harness.memory.budget as budget

    monkeypatch.setattr(
        budget,
        "_replace_images_for_estimation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert estimate_serialized_tokens(item) > 1_000


def test_provider_aware_image_estimation_preserves_multimodal_structure():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 1024, 1024)
    data_url = "data:image/png;base64," + base64.b64encode(png_header + b"x" * 100_000).decode(
        "ascii"
    )
    message = {
        "role": "user",
        "content": [
            {"type": "input_image", "image_url": data_url, "detail": "low"},
            {"type": "input_text", "text": "inspect this"},
        ],
    }
    original = copy.deepcopy(message)

    openai_tokens = estimate_message_tokens(message, model="openai/gpt-4o")
    anthropic_tokens = estimate_message_tokens(message, model="anthropic/claude-sonnet-4")

    assert message == original
    assert openai_tokens < len(data_url) // 10
    assert anthropic_tokens != openai_tokens
