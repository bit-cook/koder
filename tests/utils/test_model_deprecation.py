"""Tests for model deprecation warnings."""

from datetime import datetime, timedelta

from koder_agent.utils.model_deprecation import (
    DEPRECATION_SCHEDULE,
    check_model_deprecation,
)


class TestModelDeprecation:
    """Test model deprecation warning system."""

    def test_deprecated_model_returns_warning(self):
        """Test that known deprecated models return a warning."""
        # Test Claude 3 Opus
        warning = check_model_deprecation("claude-3-opus-20240229")
        assert warning is not None
        assert "deprecated" in warning.lower() or "retired" in warning.lower()
        assert "claude-3-opus-20240229" in warning

        # Test Claude 3.5 Haiku
        warning = check_model_deprecation("claude-3-5-haiku-20241022")
        assert warning is not None
        assert "deprecated" in warning.lower() or "retired" in warning.lower()

        # Test Claude 3.7 Sonnet
        warning = check_model_deprecation("claude-3-7-sonnet-20250219")
        assert warning is not None
        assert "deprecated" in warning.lower() or "retired" in warning.lower()

    def test_current_model_returns_none(self):
        """Test that current models return None."""
        # Test a model that should still be supported
        warning = check_model_deprecation("claude-sonnet-4-5@20250929")
        assert warning is None

        warning = check_model_deprecation("gpt-4o")
        assert warning is None

    def test_model_retiring_within_30_days_returns_warning(self):
        """Test that models retiring within 30 days return a warning."""
        # Add a model to the schedule that retires in 15 days
        future_date = datetime.now() + timedelta(days=15)
        model_name = "test-model-retiring-soon"

        # Temporarily modify the schedule
        original_schedule = DEPRECATION_SCHEDULE.copy()
        try:
            DEPRECATION_SCHEDULE[model_name] = future_date.strftime("%Y-%m-%d")

            warning = check_model_deprecation(model_name)
            assert warning is not None
            assert "retiring" in warning.lower() or "deprecated" in warning.lower()
            assert model_name in warning
        finally:
            # Restore original schedule
            DEPRECATION_SCHEDULE.clear()
            DEPRECATION_SCHEDULE.update(original_schedule)

    def test_model_retiring_after_30_days_returns_none(self):
        """Test that models retiring after 30 days return None."""
        # Add a model to the schedule that retires in 60 days
        future_date = datetime.now() + timedelta(days=60)
        model_name = "test-model-retiring-later"

        # Temporarily modify the schedule
        original_schedule = DEPRECATION_SCHEDULE.copy()
        try:
            DEPRECATION_SCHEDULE[model_name] = future_date.strftime("%Y-%m-%d")

            warning = check_model_deprecation(model_name)
            assert warning is None
        finally:
            # Restore original schedule
            DEPRECATION_SCHEDULE.clear()
            DEPRECATION_SCHEDULE.update(original_schedule)

    def test_unknown_model_returns_none(self):
        """Test that unknown models return None."""
        warning = check_model_deprecation("unknown-model-12345")
        assert warning is None

        warning = check_model_deprecation("gpt-99-ultra")
        assert warning is None

    def test_deprecation_schedule_has_required_models(self):
        """Test that the deprecation schedule includes required models."""
        assert "claude-3-opus-20240229" in DEPRECATION_SCHEDULE
        assert "claude-3-5-haiku-20241022" in DEPRECATION_SCHEDULE
        assert "claude-3-7-sonnet-20250219" in DEPRECATION_SCHEDULE
