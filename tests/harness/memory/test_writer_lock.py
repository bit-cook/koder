import asyncio
import sys
import types
from pathlib import Path

# Stub litellm before importing koder_agent to avoid optional dependency issues
if "litellm" not in sys.modules:
    litellm_stub = types.ModuleType("litellm")
    litellm_stub.model_cost = {}
    sys.modules["litellm"] = litellm_stub

project_root = Path(__file__).resolve().parents[3]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from koder_agent.harness.memory.writer_lock import TranscriptWriterLock


def test_writer_lock_blocks_concurrent_mutation(tmp_path):
    lock = TranscriptWriterLock.for_path(tmp_path / "runtime.db")
    events: list[str] = []

    async def first():
        async with lock.acquire():
            events.append("first-start")
            await asyncio.sleep(0.1)
            events.append("first-end")

    async def second():
        await asyncio.sleep(0.02)
        async with lock.acquire():
            events.append("second")

    async def run_both():
        await asyncio.gather(first(), second())

    asyncio.run(run_both())
    assert events == ["first-start", "first-end", "second"]


def test_writer_lock_reuses_singleton_per_path(tmp_path):
    first = TranscriptWriterLock.for_path(tmp_path / "runtime.db")
    second = TranscriptWriterLock.for_path(tmp_path / "runtime.db")
    assert first is second
