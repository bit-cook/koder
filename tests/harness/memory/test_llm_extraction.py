"""Tests for LLM-based memory extraction with 4-type taxonomy."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.harness.memory.extraction import (
    EXTRACTION_PROMPT,
    MEMORY_TYPES,
    extract_memories_from_messages,
    llm_extract_memories,
)
from koder_agent.harness.memory.governance import (
    MAX_EXTRACTION_CANDIDATES,
    MAX_EXTRACTION_RESPONSE_BYTES,
)


def test_memory_types_has_4():
    assert len(MEMORY_TYPES) == 4


def test_memory_types_names():
    assert "user" in MEMORY_TYPES
    assert "feedback" in MEMORY_TYPES
    assert "project" in MEMORY_TYPES
    assert "reference" in MEMORY_TYPES


def test_each_type_has_metadata():
    for name, info in MEMORY_TYPES.items():
        assert "description" in info, f"{name} missing description"
        assert "when_to_save" in info, f"{name} missing when_to_save"


def test_extraction_prompt_exists():
    assert "{conversation}" in EXTRACTION_PROMPT
    assert "user" in EXTRACTION_PROMPT
    assert "feedback" in EXTRACTION_PROMPT


def test_deterministic_extraction_still_works():
    messages = [
        {"role": "user", "content": "I'm a data scientist"},
        {"role": "assistant", "content": "Great!"},
    ]
    result = extract_memories_from_messages(messages)
    assert len(result.memories) >= 1
    assert result.memories[0]["type"] == "user"


@pytest.mark.anyio
async def test_llm_extraction_returns_typed_memories():
    messages = [
        {"role": "user", "content": "I'm a senior Go developer"},
        {"role": "assistant", "content": "Noted."},
    ]
    mock_response = json.dumps(
        [
            {"type": "user", "content": "Senior Go developer", "description": "Primary language"},
        ]
    )

    with patch(
        "koder_agent.harness.memory.extraction.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await llm_extract_memories(messages)
        assert len(result.memories) == 1
        assert result.memories[0]["type"] == "user"


@pytest.mark.anyio
async def test_llm_extraction_filters_invalid_types():
    messages = [{"role": "user", "content": "test"}]
    mock_response = json.dumps(
        [
            {"type": "user", "content": "valid", "description": "ok"},
            {"type": "invalid_type", "content": "bad", "description": "nope"},
        ]
    )

    with patch(
        "koder_agent.harness.memory.extraction.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await llm_extract_memories(messages)
        assert len(result.memories) == 1
        assert result.memories[0]["type"] == "user"


@pytest.mark.anyio
async def test_llm_extract_memories_separates_skill_candidates():
    response = json.dumps(
        {
            "memories": [{"type": "project", "content": "Use uv.", "description": "Tooling"}],
            "skill_candidates": [
                {
                    "name": "verify-first",
                    "description": "Verify focused behavior",
                    "instructions": "Run focused tests first.",
                }
            ],
        }
    )

    with patch(
        "koder_agent.harness.memory.extraction.llm_completion",
        new_callable=AsyncMock,
        return_value=response,
    ):
        result = await llm_extract_memories([{"role": "user", "content": "Use uv"}])

    assert len(result.memories) == 1
    assert len(result.skill_candidates) == 1
    assert result.skill_candidates[0]["name"] == "verify-first"


@pytest.mark.anyio
async def test_llm_extraction_handles_error():
    messages = [{"role": "user", "content": "test"}]

    with patch(
        "koder_agent.harness.memory.extraction.llm_completion",
        new_callable=AsyncMock,
        side_effect=Exception("API error"),
    ):
        result = await llm_extract_memories(messages)
        assert len(result.memories) == 0
        assert len(result.errors) == 1


@pytest.mark.anyio
async def test_llm_extraction_handles_code_fence():
    messages = [{"role": "user", "content": "test"}]
    mock_response = '```json\n[{"type": "user", "content": "test", "description": "d"}]\n```'

    with patch(
        "koder_agent.harness.memory.extraction.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await llm_extract_memories(messages)
        assert len(result.memories) == 1


@pytest.mark.anyio
async def test_llm_extraction_handles_empty_response():
    messages = [{"role": "user", "content": "test"}]

    with patch(
        "koder_agent.harness.memory.extraction.llm_completion",
        new_callable=AsyncMock,
        return_value="[]",
    ):
        result = await llm_extract_memories(messages)
        assert len(result.memories) == 0
        assert len(result.errors) == 0


@pytest.mark.anyio
async def test_llm_extraction_handles_multimodal_content():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Look at this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        },
    ]
    mock_response = json.dumps([])

    with patch(
        "koder_agent.harness.memory.extraction.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await llm_extract_memories(messages)
        assert len(result.errors) == 0


@pytest.mark.anyio
async def test_extraction_candidate_count_is_bounded():
    response = json.dumps(
        {
            "memories": [
                {"type": "project", "content": f"fact {index}", "description": "bounded"}
                for index in range(MAX_EXTRACTION_CANDIDATES + 20)
            ],
            "skill_candidates": [],
        }
    )

    with patch(
        "koder_agent.harness.memory.extraction.llm_completion",
        new_callable=AsyncMock,
        return_value=response,
    ):
        result = await llm_extract_memories([{"role": "user", "content": "facts"}])

    assert len(result.memories) == MAX_EXTRACTION_CANDIDATES
    assert any(error.startswith("candidate_limit:") for error in result.errors)


@pytest.mark.anyio
async def test_oversized_extraction_response_is_rejected_before_json_parse():
    response = " " * (MAX_EXTRACTION_RESPONSE_BYTES + 1)

    with patch(
        "koder_agent.harness.memory.extraction.llm_completion",
        new_callable=AsyncMock,
        return_value=response,
    ):
        result = await llm_extract_memories([{"role": "user", "content": "facts"}])

    assert result.memories == []
    assert "size limit" in result.errors[0]
