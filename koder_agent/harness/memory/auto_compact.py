"""Auto-compact threshold management with circuit breaker."""

from __future__ import annotations

from enum import Enum

# Buffer below the context window to trigger compaction.
# Upstream uses 13,000 tokens.
COMPACT_BUFFER = 13_000

# Maximum consecutive failures before circuit breaker trips.
MAX_CONSECUTIVE_FAILURES = 3


class TokenWarningState(Enum):
    """Token usage warning levels."""

    NONE = "none"
    WARNING = "warning"
    ERROR = "error"


class AutoCompactManager:
    """Manages auto-compact thresholds and circuit breaker state.

    Threshold formula:
        compact_threshold = (context_window - max_output_tokens) - COMPACT_BUFFER

    Circuit breaker:
        After MAX_CONSECUTIVE_FAILURES consecutive compaction failures,
        stops attempting auto-compaction. Resets on success.
    """

    def __init__(
        self,
        context_window: int,
        max_output_tokens: int = 20_000,
    ):
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens
        self.effective_window = context_window - max_output_tokens
        self.compact_threshold = self.effective_window - COMPACT_BUFFER
        self._consecutive_failures = 0

    def get_warning_state(self, current_tokens: int) -> TokenWarningState:
        """Determine warning state based on current token usage."""
        if current_tokens >= self.compact_threshold:
            return TokenWarningState.ERROR
        if current_tokens >= self.compact_threshold * 0.9:
            return TokenWarningState.WARNING
        return TokenWarningState.NONE

    def should_compact(self, current_tokens: int) -> bool:
        """Check if compaction should be triggered."""
        if self.is_circuit_broken():
            return False
        return current_tokens >= self.compact_threshold

    def record_failure(self) -> None:
        """Record a compaction failure."""
        self._consecutive_failures += 1

    def record_success(self) -> None:
        """Record a compaction success, resetting the circuit breaker."""
        self._consecutive_failures = 0

    def is_circuit_broken(self) -> bool:
        """Check if circuit breaker has tripped."""
        return self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES
