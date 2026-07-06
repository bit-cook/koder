"""Integration tests: EnhancedSQLiteSession.add_items runs micro-compaction.

These verify the wiring point described in the scheduler NOTE: tool outputs are
persisted through ``add_items``, so that override is where oversized single tool
results get truncated before hitting disk (and being re-read into future turns).
"""

import os

import pytest

from koder_agent.core.session import EnhancedSQLiteSession
from koder_agent.harness.memory.micro_compact import DEFAULT_MAX_RESULT_CHARS, ENABLED_ENV


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "micro.db")


@pytest.mark.asyncio
async def test_large_function_call_output_persisted_truncated(db_path, monkeypatch):
    """A 100k-char function_call_output persists a truncated version + marker."""
    monkeypatch.delenv(ENABLED_ENV, raising=False)
    monkeypatch.delenv("KODER_MICRO_COMPACT_MAX_CHARS", raising=False)

    session = EnhancedSQLiteSession("s-large-fco", db_path=db_path)
    big = "Z" * 100_000
    items = [
        {"type": "function_call", "call_id": "call_1", "name": "grep", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": big},
    ]
    await session.add_items(items)

    stored = await session.get_items()
    assert len(stored) == 2  # item count preserved

    fco = next(i for i in stored if i.get("type") == "function_call_output")
    assert fco["call_id"] == "call_1"  # call_id preserved
    assert len(fco["output"]) < len(big)
    assert len(fco["output"]) <= DEFAULT_MAX_RESULT_CHARS + 200
    assert "output truncated" in fco["output"]
    # Head content is preserved.
    assert fco["output"].startswith("Z")

    # The paired function_call is untouched.
    fc = next(i for i in stored if i.get("type") == "function_call")
    assert fc["call_id"] == "call_1"


@pytest.mark.asyncio
async def test_large_tool_role_content_persisted_truncated(db_path):
    """A large role=='tool' content is truncated on persist."""
    session = EnhancedSQLiteSession("s-large-tool", db_path=db_path)
    big = "Q" * 100_000
    items = [
        {"role": "user", "content": "run it"},
        {"role": "tool", "content": big, "tool_call_id": "tc9"},
    ]
    await session.add_items(items)

    stored = await session.get_items()
    assert len(stored) == 2
    tool_item = next(i for i in stored if i.get("role") == "tool")
    assert tool_item["tool_call_id"] == "tc9"
    assert len(tool_item["content"]) < len(big)
    assert "output truncated" in tool_item["content"]


@pytest.mark.asyncio
async def test_small_output_untouched(db_path):
    """Small outputs are persisted verbatim (no marker, normal operation)."""
    session = EnhancedSQLiteSession("s-small", db_path=db_path)
    items = [
        {"type": "function_call", "call_id": "c", "name": "ls", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c", "output": "small result"},
        {"role": "tool", "content": "also small", "tool_call_id": "t"},
    ]
    await session.add_items(items)

    stored = await session.get_items()
    assert len(stored) == 3
    fco = next(i for i in stored if i.get("type") == "function_call_output")
    assert fco["output"] == "small result"
    assert "truncated" not in fco["output"]
    tool_item = next(i for i in stored if i.get("role") == "tool")
    assert tool_item["content"] == "also small"


@pytest.mark.asyncio
async def test_call_ids_and_count_preserved_multi(db_path):
    """Item count and all call ids survive a mixed batch."""
    session = EnhancedSQLiteSession("s-multi", db_path=db_path)
    items = [
        {"role": "user", "content": "go"},
        {"type": "function_call", "call_id": "a", "name": "f", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "a", "output": "A" * 60_000},
        {"type": "function_call", "call_id": "b", "name": "g", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "b", "output": "ok"},
    ]
    await session.add_items(items)

    stored = await session.get_items()
    assert len(stored) == 5

    call_ids = [i.get("call_id") for i in stored if i.get("call_id")]
    # 2 function_call + 2 function_call_output, ids a,a,b,b
    assert sorted(call_ids) == ["a", "a", "b", "b"]

    outputs = {i["call_id"]: i["output"] for i in stored if i.get("type") == "function_call_output"}
    assert "output truncated" in outputs["a"]
    assert outputs["b"] == "ok"


@pytest.mark.asyncio
async def test_disabled_via_env_persists_full_output(db_path, monkeypatch):
    """When KODER_MICRO_COMPACT is off, large outputs persist untruncated."""
    monkeypatch.setenv(ENABLED_ENV, "0")

    session = EnhancedSQLiteSession("s-disabled", db_path=db_path)
    big = "W" * 60_000
    items = [
        {"type": "function_call_output", "call_id": "c", "output": big},
    ]
    await session.add_items(items)

    stored = await session.get_items()
    fco = next(i for i in stored if i.get("type") == "function_call_output")
    assert fco["output"] == big  # untouched
    assert "truncated" not in fco["output"]


@pytest.mark.asyncio
async def test_custom_threshold_via_env(db_path, monkeypatch):
    """KODER_MICRO_COMPACT_MAX_CHARS controls the truncation threshold."""
    monkeypatch.setenv("KODER_MICRO_COMPACT_MAX_CHARS", "1000")

    session = EnhancedSQLiteSession("s-threshold", db_path=db_path)
    output = "M" * 5000  # under default (20k) but over custom 1000
    items = [
        {"type": "function_call_output", "call_id": "c", "output": output},
    ]
    await session.add_items(items)

    stored = await session.get_items()
    fco = next(i for i in stored if i.get("type") == "function_call_output")
    assert "output truncated" in fco["output"]
    # Head kept ~1000 chars + marker.
    assert len(fco["output"]) < 1300


@pytest.mark.asyncio
async def test_empty_items_noop(db_path):
    """Empty add_items call is a safe no-op."""
    session = EnhancedSQLiteSession("s-empty", db_path=db_path)
    await session.add_items([])
    stored = await session.get_items()
    assert stored == []


@pytest.mark.asyncio
async def test_original_items_not_mutated(db_path):
    """add_items must not mutate the caller's list/dicts in place."""
    session = EnhancedSQLiteSession("s-nomutate", db_path=db_path)
    big = "X" * 60_000
    original = {"type": "function_call_output", "call_id": "c", "output": big}
    items = [original]
    await session.add_items(items)
    assert original["output"] == big  # caller's dict unchanged


def test_env_default_when_unset(monkeypatch):
    """Sanity: with no env override the threshold is the documented default."""
    monkeypatch.delenv("KODER_MICRO_COMPACT_MAX_CHARS", raising=False)
    assert os.environ.get("KODER_MICRO_COMPACT_MAX_CHARS") is None
    from koder_agent.harness.memory.micro_compact import MicroCompactConfig

    assert MicroCompactConfig.from_env().max_result_chars == DEFAULT_MAX_RESULT_CHARS
