"""Tests for scheduler + compaction correctness fixes.

Covers four verified-OPEN items:

1. goal-continuation-runaway: the automatic goal-continuation loop is bounded by
   a cumulative-token guard (primary), not just the continuation count cap.
2. context-overflow-not-retryable: a CONTEXT_OVERFLOW turn triggers exactly one
   auto-compaction + re-run, then gives up.
3. autocompact-record-failure-on-noop: a legitimate compaction no-op (history
   already minimal) does NOT advance the circuit breaker.
4. llm-compact-tail / todo pinning: the active todo list is pinned verbatim into
   the compacted head.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from koder_agent.harness.memory.auto_compact import AutoCompactManager
from koder_agent.harness.memory.compact import CompactionResult


@contextmanager
def _patched_scheduler_env():
    """Patch out heavy scheduler dependencies and yield an in-memory session mock."""
    with (
        patch("koder_agent.core.scheduler.get_all_tools", return_value=[]),
        patch("koder_agent.core.scheduler.get_display_hooks"),
        patch("koder_agent.core.scheduler.ApprovalHooks"),
        patch("koder_agent.core.scheduler.EnhancedSQLiteSession") as mock_session_cls,
        patch("koder_agent.core.scheduler.get_companion", return_value=None),
    ):
        mock_session = AsyncMock()
        mock_session.session_id = "test"
        mock_session.db_path = ":memory:"
        mock_session.get_items = AsyncMock(return_value=[])
        mock_session.replace_items = AsyncMock()
        mock_session.add_items = AsyncMock()
        mock_session.summarization_threshold = None
        mock_session_cls.return_value = mock_session
        yield mock_session


def _make_scheduler():
    from koder_agent.core.scheduler import AgentScheduler

    return AgentScheduler(session_id="test")


# ---------------------------------------------------------------------------
# Item 1: goal continuation cumulative-token guard
# ---------------------------------------------------------------------------


class TestGoalContinuationTokenGuard:
    @pytest.mark.asyncio
    async def test_loop_stops_at_token_cap(self, monkeypatch):
        """An unbudgeted ACTIVE goal that never ends is bounded by the token cap.

        The count cap is high; each continuation burns tokens, so the loop must
        break on the cumulative-token guard well before the count backstop.
        """
        monkeypatch.setenv("KODER_GOAL_MAX_CONTINUATION_TOKENS", "1000")
        monkeypatch.setenv("KODER_GOAL_MAX_CONTINUATIONS", "1000")

        with _patched_scheduler_env():
            scheduler = _make_scheduler()

            # The goal never leaves ACTIVE, so a prompt is always available.
            scheduler.goal_runtime.next_continuation_prompt = AsyncMock(return_value="keep going")

            calls = 0

            async def run_turn(_prompt: str) -> str:
                nonlocal calls
                calls += 1
                # Each continuation turn burns 400 tokens.
                scheduler.usage_tracker.session_usage.input_tokens += 400
                return f"turn {calls}"

            result = await scheduler._run_goal_continuations("initial", run_turn)

        # baseline=0; after 3 turns cumulative=1200 > 1000 cap, so the 4th check
        # breaks the loop. Well under the count cap of 1000.
        assert calls == 3
        assert result == "turn 3"

    @pytest.mark.asyncio
    async def test_count_cap_still_applies_as_backstop(self, monkeypatch):
        """With the token cap disabled, the count cap remains the backstop."""
        monkeypatch.setenv("KODER_GOAL_MAX_CONTINUATION_TOKENS", "0")  # disable token guard
        monkeypatch.setenv("KODER_GOAL_MAX_CONTINUATIONS", "5")

        with _patched_scheduler_env():
            scheduler = _make_scheduler()
            scheduler.goal_runtime.next_continuation_prompt = AsyncMock(return_value="keep going")

            calls = 0

            async def run_turn(_prompt: str) -> str:
                nonlocal calls
                calls += 1
                scheduler.usage_tracker.session_usage.input_tokens += 10_000
                return f"turn {calls}"

            await scheduler._run_goal_continuations("initial", run_turn)

        assert calls == 5  # bounded by count cap, not tokens (guard disabled)

    @pytest.mark.asyncio
    async def test_no_runaway_when_goal_becomes_inactive(self, monkeypatch):
        """Non-regression: a goal that stops asking ends the loop immediately."""
        monkeypatch.setenv("KODER_GOAL_MAX_CONTINUATION_TOKENS", "1000000")
        monkeypatch.setenv("KODER_GOAL_MAX_CONTINUATIONS", "25")

        with _patched_scheduler_env():
            scheduler = _make_scheduler()
            # No further continuation requested -> loop should not run at all.
            scheduler.goal_runtime.next_continuation_prompt = AsyncMock(return_value=None)

            run_turn = AsyncMock(return_value="should not run")
            result = await scheduler._run_goal_continuations("done", run_turn)

        run_turn.assert_not_called()
        assert result == "done"


# ---------------------------------------------------------------------------
# Item 2: context overflow triggers one compaction + retry, then gives up
# ---------------------------------------------------------------------------


class _ContextOverflowError(Exception):
    """Exception whose message classifies as CONTEXT_OVERFLOW."""

    def __init__(self):
        super().__init__("This model's maximum context length is 200000 tokens")


class TestContextOverflowRetry:
    @pytest.mark.asyncio
    async def test_overflow_triggers_one_compaction_then_succeeds(self):
        """First run overflows -> compact once -> re-run succeeds."""
        with _patched_scheduler_env():
            scheduler = _make_scheduler()
            scheduler.dev_agent = object()
            scheduler._agent_initialized = True
            scheduler._migration_done = True
            scheduler._auto_compact = AutoCompactManager(
                context_window=200_000, max_output_tokens=20_000
            )
            scheduler._capture_usage = AsyncMock()
            scheduler._refresh_magic_docs_after_turn = AsyncMock()
            scheduler._repair_unreplayable_session_items = AsyncMock()
            scheduler._run_auto_compact = AsyncMock()
            scheduler._finish_goal_turn = AsyncMock()

            attempts = {"n": 0}

            class _Result:
                final_output = "recovered output"
                context_wrapper = None

            async def fake_run(*_a, **_k):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise _ContextOverflowError()
                return _Result()

            with patch("koder_agent.core.scheduler.Runner.run", side_effect=fake_run):
                response = await scheduler.handle("do the thing", render_output=False)
            await scheduler.cleanup()

        assert attempts["n"] == 2  # original + one retry
        scheduler._run_auto_compact.assert_called_once()
        assert "recovered output" in response

    @pytest.mark.asyncio
    async def test_overflow_twice_gives_up_after_single_retry(self):
        """A second overflow falls through to normal error handling (no loop)."""
        with _patched_scheduler_env():
            scheduler = _make_scheduler()
            scheduler.dev_agent = object()
            scheduler._agent_initialized = True
            scheduler._migration_done = True
            scheduler._auto_compact = AutoCompactManager(
                context_window=200_000, max_output_tokens=20_000
            )
            scheduler._capture_usage = AsyncMock()
            scheduler._refresh_magic_docs_after_turn = AsyncMock()
            scheduler._repair_unreplayable_session_items = AsyncMock()
            scheduler._run_auto_compact = AsyncMock()
            scheduler._finish_goal_turn = AsyncMock()

            attempts = {"n": 0}

            async def always_overflow(*_a, **_k):
                attempts["n"] += 1
                raise _ContextOverflowError()

            with patch("koder_agent.core.scheduler.Runner.run", side_effect=always_overflow):
                response = await scheduler.handle("do the thing", render_output=False)
            await scheduler.cleanup()

        # Exactly one retry: original attempt + one post-compact attempt.
        assert attempts["n"] == 2
        scheduler._run_auto_compact.assert_called_once()
        assert "Execution error" in response
        assert "Please provide new instructions" in response

    @pytest.mark.asyncio
    async def test_non_overflow_error_is_not_retried(self):
        """Non-regression: a generic error is NOT compacted/retried."""
        with _patched_scheduler_env():
            scheduler = _make_scheduler()
            scheduler.dev_agent = object()
            scheduler._agent_initialized = True
            scheduler._migration_done = True
            scheduler._auto_compact = AutoCompactManager(
                context_window=200_000, max_output_tokens=20_000
            )
            scheduler._capture_usage = AsyncMock()
            scheduler._refresh_magic_docs_after_turn = AsyncMock()
            scheduler._repair_unreplayable_session_items = AsyncMock()
            scheduler._run_auto_compact = AsyncMock()
            scheduler._finish_goal_turn = AsyncMock()

            attempts = {"n": 0}

            async def boom(*_a, **_k):
                attempts["n"] += 1
                raise RuntimeError("something unrelated broke")

            with patch("koder_agent.core.scheduler.Runner.run", side_effect=boom):
                response = await scheduler.handle("do the thing", render_output=False)
            await scheduler.cleanup()

        assert attempts["n"] == 1  # no retry
        scheduler._run_auto_compact.assert_not_called()
        assert "Execution error" in response

    @pytest.mark.asyncio
    async def test_overflow_not_retried_when_circuit_broken(self):
        """Non-regression: a broken breaker skips the compaction+retry."""
        with _patched_scheduler_env():
            scheduler = _make_scheduler()
            scheduler.dev_agent = object()
            scheduler._agent_initialized = True
            scheduler._migration_done = True
            scheduler._auto_compact = AutoCompactManager(
                context_window=200_000, max_output_tokens=20_000
            )
            # Trip the breaker.
            scheduler._auto_compact.record_failure()
            scheduler._auto_compact.record_failure()
            scheduler._auto_compact.record_failure()
            assert scheduler._auto_compact.is_circuit_broken()

            scheduler._capture_usage = AsyncMock()
            scheduler._refresh_magic_docs_after_turn = AsyncMock()
            scheduler._repair_unreplayable_session_items = AsyncMock()
            scheduler._run_auto_compact = AsyncMock()
            scheduler._finish_goal_turn = AsyncMock()

            attempts = {"n": 0}

            async def always_overflow(*_a, **_k):
                attempts["n"] += 1
                raise _ContextOverflowError()

            with patch("koder_agent.core.scheduler.Runner.run", side_effect=always_overflow):
                response = await scheduler.handle("do the thing", render_output=False)
            await scheduler.cleanup()

        assert attempts["n"] == 1  # no retry when circuit broken
        scheduler._run_auto_compact.assert_not_called()
        assert "Execution error" in response


# ---------------------------------------------------------------------------
# Item 3: no-op compaction does not trip the breaker
# ---------------------------------------------------------------------------


class TestAutoCompactNoOpBreaker:
    @pytest.mark.asyncio
    async def test_noop_compaction_does_not_advance_breaker(self):
        """A no-op (no summary, kept == original) must not record a failure."""
        minimal = [{"role": "user", "content": "hello"}]
        with (
            _patched_scheduler_env() as mock_session,
            patch("koder_agent.core.scheduler.llm_compact_messages") as mock_compact,
        ):
            mock_session.get_items = AsyncMock(return_value=list(minimal))
            mock_compact.return_value = CompactionResult(
                summary=None,
                kept_messages=list(minimal),
                token_count=10,
                original_count=1,
            )
            scheduler = _make_scheduler()
            scheduler._auto_compact = AutoCompactManager(
                context_window=50_000, max_output_tokens=10_000
            )

            # Three no-op compactions in a row must NOT trip the breaker.
            for _ in range(3):
                await scheduler._run_auto_compact()

            assert scheduler._auto_compact._consecutive_failures == 0
            assert not scheduler._auto_compact.is_circuit_broken()
            # No-op means the session was never rewritten.
            mock_session.replace_items.assert_not_called()

    @pytest.mark.asyncio
    async def test_genuine_llm_failure_still_records_failure(self):
        """Non-regression: an actual compaction error still trips the breaker."""
        with (
            _patched_scheduler_env() as mock_session,
            patch(
                "koder_agent.core.scheduler.llm_compact_messages",
                side_effect=RuntimeError("LLM unavailable"),
            ),
        ):
            mock_session.get_items = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
            scheduler = _make_scheduler()
            scheduler._auto_compact = AutoCompactManager(
                context_window=50_000, max_output_tokens=10_000
            )

            await scheduler._run_auto_compact()

            assert scheduler._auto_compact._consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_replace_items_failure_still_records_failure(self):
        """Non-regression: an atomic replacement failure is recorded."""
        original = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new"},
            {"role": "assistant", "content": "new answer"},
        ]
        with (
            _patched_scheduler_env() as mock_session,
            patch("koder_agent.core.scheduler.llm_compact_messages") as mock_compact,
        ):
            mock_session.get_items = AsyncMock(return_value=list(original))
            mock_session.replace_items = AsyncMock(side_effect=RuntimeError("disk full"))
            mock_compact.return_value = CompactionResult(
                summary="summarized old turns",
                kept_messages=[{"role": "assistant", "content": "new answer"}],
                token_count=50,
                original_count=4,
            )
            scheduler = _make_scheduler()
            scheduler._auto_compact = AutoCompactManager(
                context_window=50_000, max_output_tokens=10_000
            )

            await scheduler._run_auto_compact()

            assert scheduler._auto_compact._consecutive_failures == 1


# ---------------------------------------------------------------------------
# Item 4 (scheduler half): the active todo list is pinned across compaction
# ---------------------------------------------------------------------------


class TestTodoPinnedAcrossCompaction:
    @pytest.mark.asyncio
    async def test_active_todo_pinned_into_compacted_head(self):
        original = [
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new request"},
            {"role": "assistant", "content": "new answer"},
        ]
        with (
            _patched_scheduler_env() as mock_session,
            patch("koder_agent.core.scheduler.llm_compact_messages") as mock_compact,
        ):
            mock_session.get_items = AsyncMock(return_value=list(original))
            mock_compact.return_value = CompactionResult(
                summary="the earlier conversation",
                kept_messages=[{"role": "assistant", "content": "new answer"}],
                token_count=50,
                original_count=4,
            )
            scheduler = _make_scheduler()
            scheduler.todo_store.todos = [
                {
                    "content": "wire up the parser",
                    "status": "in_progress",
                    "priority": "high",
                    "id": "1",
                },
                {
                    "content": "add regression tests",
                    "status": "pending",
                    "priority": "medium",
                    "id": "2",
                },
            ]
            scheduler._auto_compact = AutoCompactManager(
                context_window=50_000, max_output_tokens=10_000
            )

            await scheduler._run_auto_compact()

            added = mock_session.replace_items.call_args[0][0]

        # Head: summary first, pinned todo list second.
        assert added[0]["content"].startswith("[Conversation compacted]")
        pinned = added[1]
        assert set(pinned.keys()) == {"role", "content"}  # replayable
        assert "wire up the parser" in pinned["content"]
        assert "add regression tests" in pinned["content"]
        # The kept tail still follows.
        assert added[-1]["content"] == "new answer"

    @pytest.mark.asyncio
    async def test_no_todo_message_when_store_empty(self):
        """Non-regression: an empty todo store adds no pinned message."""
        original = [
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new request"},
            {"role": "assistant", "content": "new answer"},
        ]
        with (
            _patched_scheduler_env() as mock_session,
            patch("koder_agent.core.scheduler.llm_compact_messages") as mock_compact,
        ):
            mock_session.get_items = AsyncMock(return_value=list(original))
            mock_compact.return_value = CompactionResult(
                summary="the earlier conversation",
                kept_messages=[{"role": "assistant", "content": "new answer"}],
                token_count=50,
                original_count=4,
            )
            scheduler = _make_scheduler()
            scheduler.todo_store.todos = []
            scheduler._auto_compact = AutoCompactManager(
                context_window=50_000, max_output_tokens=10_000
            )

            await scheduler._run_auto_compact()

            added = mock_session.replace_items.call_args[0][0]

        # summary + single kept message, no pinned plan injected.
        assert len(added) == 2
        assert added[0]["content"].startswith("[Conversation compacted]")
        assert added[1]["content"] == "new answer"
