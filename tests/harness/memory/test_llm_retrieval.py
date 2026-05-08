"""Tests for LLM-based memory retrieval."""

from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.harness.memory.retrieval import (
    llm_retrieve_relevant_memories,
    retrieve_relevant_memories,
)


@pytest.fixture
def memory_dir(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "user_role.md").write_text(
        "---\ntype: user\ndescription: User is a Go developer\n---\nUser has 10 years of Go experience.\n"
    )
    (mem_dir / "feedback_testing.md").write_text(
        "---\ntype: feedback\ndescription: No mocks in integration tests\n---\nDon't use mocks. Reason: prior prod incident.\n"
    )
    (mem_dir / "project_auth.md").write_text(
        "---\ntype: project\ndescription: Auth rewrite for compliance\n---\nReplacing auth middleware for legal compliance.\n"
    )
    return mem_dir


def test_keyword_retrieval_still_works(memory_dir):
    result = retrieve_relevant_memories(
        "Go developer testing",
        [memory_dir],
        max_tokens=5000,
    )
    assert len(result.memories) >= 1


@pytest.mark.asyncio
async def test_llm_retrieval_selects_relevant(memory_dir):
    mock_response = "user_role.md\nfeedback_testing.md"

    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await llm_retrieve_relevant_memories(
            "I'm writing Go tests, what should I know?",
            [memory_dir],
            max_tokens=5000,
            max_files=5,
        )
        assert len(result.memories) >= 1
        paths = [m.path.name for m in result.memories]
        assert "user_role.md" in paths


@pytest.mark.asyncio
async def test_llm_retrieval_respects_max_files(memory_dir):
    mock_response = "user_role.md\nfeedback_testing.md\nproject_auth.md"

    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await llm_retrieve_relevant_memories(
            "everything",
            [memory_dir],
            max_tokens=50000,
            max_files=1,
        )
        assert len(result.memories) <= 1


@pytest.mark.asyncio
async def test_llm_retrieval_respects_token_budget(memory_dir):
    # Create a large memory file
    (memory_dir / "huge.md").write_text(
        "---\ntype: user\ndescription: huge file\n---\n" + "x" * 100000
    )
    mock_response = "huge.md"

    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await llm_retrieve_relevant_memories(
            "test",
            [memory_dir],
            max_tokens=100,
            max_files=5,
        )
        # Should either skip the huge file or truncate
        assert result.token_count <= 200  # Some tolerance


@pytest.mark.asyncio
async def test_llm_retrieval_falls_back_on_error(memory_dir):
    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        side_effect=Exception("API error"),
    ):
        result = await llm_retrieve_relevant_memories(
            "Go developer",
            [memory_dir],
            max_tokens=5000,
        )
        # Should fall back to keyword retrieval
        assert len(result.memories) >= 1


@pytest.mark.asyncio
async def test_llm_retrieval_handles_none_response(memory_dir):
    mock_response = "NONE"

    with patch(
        "koder_agent.utils.client.llm_completion",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await llm_retrieve_relevant_memories(
            "irrelevant query xyz",
            [memory_dir],
            max_tokens=5000,
        )
        assert len(result.memories) == 0


@pytest.mark.asyncio
async def test_llm_retrieval_handles_empty_dir(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    result = await llm_retrieve_relevant_memories(
        "test",
        [empty_dir],
        max_tokens=5000,
    )
    assert len(result.memories) == 0
