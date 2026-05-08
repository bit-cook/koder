"""Tests for auto-compact threshold management."""

from koder_agent.harness.memory.auto_compact import (
    COMPACT_BUFFER,
    MAX_CONSECUTIVE_FAILURES,
    AutoCompactManager,
    TokenWarningState,
)


def test_constants():
    assert COMPACT_BUFFER == 13_000
    assert MAX_CONSECUTIVE_FAILURES == 3


def test_threshold_calculation():
    mgr = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    # effective = 200k - 20k = 180k; threshold = 180k - 13k = 167k
    assert mgr.compact_threshold == 167_000


def test_threshold_calculation_small_window():
    mgr = AutoCompactManager(context_window=32_000, max_output_tokens=4_000)
    # effective = 32k - 4k = 28k; threshold = 28k - 13k = 15k
    assert mgr.compact_threshold == 15_000


def test_warning_state_none():
    mgr = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    assert mgr.get_warning_state(50_000) == TokenWarningState.NONE


def test_warning_state_warning():
    mgr = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    # threshold=167k, warning at 90% = ~150k
    assert mgr.get_warning_state(155_000) == TokenWarningState.WARNING


def test_warning_state_error():
    mgr = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    assert mgr.get_warning_state(168_000) == TokenWarningState.ERROR


def test_should_compact_below_threshold():
    mgr = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    assert not mgr.should_compact(50_000)


def test_should_compact_above_threshold():
    mgr = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    assert mgr.should_compact(170_000)


def test_circuit_breaker_trips_after_3():
    mgr = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    assert not mgr.is_circuit_broken()
    mgr.record_failure()
    mgr.record_failure()
    assert not mgr.is_circuit_broken()
    mgr.record_failure()
    assert mgr.is_circuit_broken()


def test_circuit_breaker_resets_on_success():
    mgr = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    mgr.record_failure()
    mgr.record_failure()
    mgr.record_failure()
    assert mgr.is_circuit_broken()
    mgr.record_success()
    assert not mgr.is_circuit_broken()


def test_should_compact_blocked_by_circuit_breaker():
    mgr = AutoCompactManager(context_window=200_000, max_output_tokens=20_000)
    mgr.record_failure()
    mgr.record_failure()
    mgr.record_failure()
    # Even above threshold, should NOT compact if circuit is broken
    assert not mgr.should_compact(999_999)
