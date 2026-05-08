"""Concurrent tool execution orchestration.

Read-only tools run concurrently (up to MAX_CONCURRENT_READS).
Write tools run serially to prevent conflicts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

# Tools that are safe to run concurrently (no side effects)
READ_ONLY_TOOL_NAMES = frozenset(
    {
        "read_file",
        "list_directory",
        "glob_search",
        "grep_search",
        "code_intelligence",
        "web_search",
        "web_fetch",
        "todo_read",
        "get_skill",
        "tool_search",
        "sleep_tool",
        "list_mcp_resources",
        "read_mcp_resource",
    }
)

MAX_CONCURRENT_READS = 10


@dataclass
class ToolBatch:
    """A batch of tool calls to execute together."""

    calls: list[dict]
    concurrent: bool  # True if all calls are read-only


class ToolOrchestrator:
    """Orchestrates tool execution with concurrent read batching."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_READS):
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def is_read_only(self, tool_name: str) -> bool:
        return tool_name in READ_ONLY_TOOL_NAMES

    def partition_calls(self, calls: list[dict]) -> list[ToolBatch]:
        """Partition tool calls into batches.

        Consecutive read-only calls are grouped into concurrent batches.
        Write calls each get their own serial batch.
        """
        if not calls:
            return []

        batches: list[ToolBatch] = []
        current_reads: list[dict] = []

        for call in calls:
            if self.is_read_only(call.get("tool", "")):
                current_reads.append(call)
            else:
                # Flush any pending reads
                if current_reads:
                    batches.append(ToolBatch(calls=current_reads, concurrent=True))
                    current_reads = []
                # Write call gets its own serial batch
                batches.append(ToolBatch(calls=[call], concurrent=False))

        # Flush remaining reads
        if current_reads:
            batches.append(ToolBatch(calls=current_reads, concurrent=True))

        return batches

    async def execute_batch(
        self,
        calls: list[dict],
        executor: Callable[[str, dict], Awaitable[Any]],
    ) -> list[Any]:
        """Execute a list of tool calls with read/write batching.

        Args:
            calls: List of dicts with 'tool' and 'args' keys.
            executor: Async function(tool_name, args) that executes a single tool.

        Returns:
            Results in the same order as the input calls.
        """
        batches = self.partition_calls(calls)
        all_results: list[Any] = []

        for batch in batches:
            if batch.concurrent and len(batch.calls) > 1:
                # Run read-only calls concurrently
                async def _run_with_sem(call):
                    async with self._semaphore:
                        return await executor(call["tool"], call.get("args", {}))

                results = await asyncio.gather(*[_run_with_sem(call) for call in batch.calls])
                all_results.extend(results)
            else:
                # Run serially (write calls or single call)
                for call in batch.calls:
                    result = await executor(call["tool"], call.get("args", {}))
                    all_results.append(result)

        return all_results
