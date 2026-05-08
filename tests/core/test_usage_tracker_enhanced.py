"""Tests for enhanced per-model cost tracking."""

from koder_agent.core.usage_tracker import (
    ModelUsage,
    UsageTracker,
)


def test_model_usage_dataclass():
    mu = ModelUsage(model="claude-sonnet-4-6")
    assert mu.input_tokens == 0
    assert mu.output_tokens == 0
    assert mu.cache_read_tokens == 0
    assert mu.cache_write_tokens == 0
    assert mu.cost == 0.0
    assert mu.request_count == 0


def test_record_with_model():
    tracker = UsageTracker()
    tracker.record_usage(1000, 500, model="claude-sonnet-4-6")
    tracker.record_usage(2000, 800, model="claude-sonnet-4-6")
    tracker.record_usage(500, 200, model="gpt-4o")

    assert tracker.session_usage.input_tokens == 3500
    assert tracker.session_usage.output_tokens == 1500
    assert tracker.session_usage.request_count == 3

    # Per-model breakdown
    models = tracker.get_per_model_usage()
    assert "claude-sonnet-4-6" in models
    assert models["claude-sonnet-4-6"].input_tokens == 3000
    assert models["claude-sonnet-4-6"].output_tokens == 1300
    assert models["claude-sonnet-4-6"].request_count == 2
    assert "gpt-4o" in models
    assert models["gpt-4o"].input_tokens == 500


def test_record_cache_tokens():
    tracker = UsageTracker()
    tracker.record_usage(
        1000,
        500,
        model="claude-sonnet-4-6",
        cache_read_tokens=800,
        cache_write_tokens=200,
    )

    models = tracker.get_per_model_usage()
    assert models["claude-sonnet-4-6"].cache_read_tokens == 800
    assert models["claude-sonnet-4-6"].cache_write_tokens == 200


def test_backward_compatible_record():
    """record_usage without model param should still work."""
    tracker = UsageTracker()
    tracker.record_usage(1000, 500)
    assert tracker.session_usage.input_tokens == 1000
    assert tracker.session_usage.output_tokens == 500


def test_save_and_load(tmp_path):
    tracker = UsageTracker()
    tracker.record_usage(1000, 500, model="claude-sonnet-4-6")
    tracker.record_usage(2000, 800, model="gpt-4o")

    save_path = tmp_path / "usage.json"
    tracker.save(save_path)
    assert save_path.exists()

    # Load into new tracker
    tracker2 = UsageTracker()
    tracker2.load(save_path)
    assert tracker2.session_usage.input_tokens == 3000
    assert tracker2.session_usage.request_count == 2
    models = tracker2.get_per_model_usage()
    assert "claude-sonnet-4-6" in models
    assert "gpt-4o" in models


def test_save_creates_parent_dirs(tmp_path):
    tracker = UsageTracker()
    tracker.record_usage(100, 50, model="test")
    save_path = tmp_path / "deep" / "nested" / "usage.json"
    tracker.save(save_path)
    assert save_path.exists()


def test_load_nonexistent_file(tmp_path):
    tracker = UsageTracker()
    tracker.load(tmp_path / "nonexistent.json")
    # Should not crash, just stay empty
    assert tracker.session_usage.request_count == 0


def test_format_summary():
    tracker = UsageTracker()
    tracker.record_usage(10000, 5000, model="claude-sonnet-4-6")
    tracker.record_usage(3000, 1000, model="gpt-4o")

    summary = tracker.format_summary()
    assert "claude-sonnet-4-6" in summary or "sonnet" in summary.lower()
    assert isinstance(summary, str)
    assert len(summary) > 0
