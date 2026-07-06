"""Token usage and cost tracking for API calls."""

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import litellm

from ..utils.client import get_model_name
from ..utils.model_info import get_model_name_variants_for_lookup

logger = logging.getLogger(__name__)


def usage_snapshot_path(session_id: str, *, home: Path | None = None) -> Path:
    """Return the durable usage snapshot path for a session id."""
    root = home or Path.home()
    safe_session_id = quote(session_id or "default", safe="")
    return root / ".koder" / "usage" / f"{safe_session_id}.json"


@dataclass
class SessionUsage:
    """Tracks cumulative token usage and cost for a session."""

    input_tokens: int = 0  # Cumulative input tokens
    output_tokens: int = 0  # Cumulative output tokens
    total_cost: float = 0.0
    request_count: int = 0
    last_input_tokens: int = 0  # Last API call's input tokens
    last_output_tokens: int = 0  # Last API call's output tokens
    current_context_tokens: int = 0  # Estimated current context size (tokens)
    cache_read_tokens: int = 0  # Cumulative cache-read (prompt cache hit) tokens
    cache_write_tokens: int = 0  # Cumulative cache-write (prompt cache creation) tokens


@dataclass
class UsageSummary:
    """A snapshot of session usage suitable for /cost-style reporting.

    ``cost_unavailable`` is set when per-token pricing is unknown (e.g. a
    subscription/OAuth model that litellm has no price for). In that case
    ``total_cost`` is ``0.0`` and callers should surface the token counts while
    marking the dollar figure as unavailable rather than printing ``$0.00``.
    """

    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    context_tokens: int = 0
    total_cost: float = 0.0
    cost_unavailable: bool = False

    @property
    def fresh_input_tokens(self) -> int:
        """Input tokens NOT served from the prompt cache.

        API-reported ``input_tokens`` already excludes cache reads, so this is
        simply ``input_tokens`` clamped at zero. Exposed as a named accessor so
        callers can express the "fresh vs cached" split clearly.
        """
        return max(0, self.input_tokens)


@dataclass
class ModelUsage:
    """Per-model token usage breakdown."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0
    request_count: int = 0


class UsageTracker:
    """Tracks and calculates token usage and costs."""

    def __init__(self):
        self.session_usage = SessionUsage()
        self._model: Optional[str] = None
        self._cached_costs: Optional[tuple[float, float]] = None
        self._per_model: dict[str, ModelUsage] = {}

    @property
    def model(self) -> str:
        """Get the current model name, caching for performance."""
        if self._model is None:
            self._model = get_model_name()
        return self._model

    def get_model_costs(self) -> tuple[float, float]:
        """
        Get input and output cost per token for current model.

        Returns:
            Tuple of (input_cost_per_token, output_cost_per_token)
        """
        # Return cached costs if available (costs don't change per request)
        if self._cached_costs is not None:
            return self._cached_costs

        variants = get_model_name_variants_for_lookup(self.model)

        for name in variants:
            try:
                info = litellm.model_cost.get(name, {})
                input_cost = info.get("input_cost_per_token", 0.0)
                output_cost = info.get("output_cost_per_token", 0.0)
                if input_cost > 0 or output_cost > 0:
                    self._cached_costs = (input_cost, output_cost)
                    return self._cached_costs
            except Exception:
                logger.debug("Failed to lookup model cost info", exc_info=True)
                continue

        self._cached_costs = (0.0, 0.0)
        return self._cached_costs

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """
        Calculate cost for given token counts.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Total cost in USD
        """
        input_cost_per_token, output_cost_per_token = self.get_model_costs()
        return (input_tokens * input_cost_per_token) + (output_tokens * output_cost_per_token)

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        context_tokens: Optional[int] = None,
        model: Optional[str] = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        """
        Record usage from an API call.

        Args:
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens generated
            context_tokens: Optional explicit context size (if different from input+output)
            model: Optional model name for per-model tracking
            cache_read_tokens: Number of cache read tokens (e.g., Anthropic prompt caching)
            cache_write_tokens: Number of cache write tokens
        """
        cost = self.calculate_cost(input_tokens, output_tokens)
        self.session_usage.input_tokens += input_tokens
        self.session_usage.output_tokens += output_tokens
        self.session_usage.cache_read_tokens += cache_read_tokens
        self.session_usage.cache_write_tokens += cache_write_tokens
        self.session_usage.total_cost += cost
        self.session_usage.request_count += 1
        self.session_usage.last_input_tokens = input_tokens
        self.session_usage.last_output_tokens = output_tokens

        if context_tokens is not None:
            self.session_usage.current_context_tokens = context_tokens
        else:
            # Fallback: assume context is roughly input + output of the run
            self.session_usage.current_context_tokens = input_tokens + output_tokens

        # Track per-model usage if model is provided
        if model:
            if model not in self._per_model:
                self._per_model[model] = ModelUsage(model=model)

            model_usage = self._per_model[model]
            model_usage.input_tokens += input_tokens
            model_usage.output_tokens += output_tokens
            model_usage.cache_read_tokens += cache_read_tokens
            model_usage.cache_write_tokens += cache_write_tokens
            model_usage.cost += cost
            model_usage.request_count += 1

    def get_per_model_usage(self) -> dict[str, ModelUsage]:
        """
        Get per-model usage breakdown.

        Returns:
            Dictionary mapping model names to ModelUsage instances
        """
        return self._per_model

    def pricing_known(self) -> bool:
        """Return True when per-token pricing is known for the current model.

        On subscription/OAuth backends (or unrecognized models) litellm has no
        price, so both per-token costs are zero. Callers use this to decide
        whether to render a dollar figure or mark cost as unavailable.
        """
        input_cost, output_cost = self.get_model_costs()
        return input_cost > 0 or output_cost > 0

    def summary(self) -> "UsageSummary":
        """Return a structured usage snapshot for /cost-style reporting.

        Aggregates cache-read/cache-write tokens across all recorded requests
        (falling back to per-model totals for snapshots recorded before those
        counters existed) and flags ``cost_unavailable`` when the current model
        has no known per-token pricing.

        Returns:
            A :class:`UsageSummary` with token counts, context size, cost, and
            a ``cost_unavailable`` flag.
        """
        usage = self.session_usage

        cache_read = getattr(usage, "cache_read_tokens", 0)
        cache_write = getattr(usage, "cache_write_tokens", 0)

        # Backfill from per-model totals for snapshots that predate session-level
        # cache counters (older saved usage files, or callers that only recorded
        # per-model cache tokens).
        if cache_read == 0 and cache_write == 0 and self._per_model:
            cache_read = sum(m.cache_read_tokens for m in self._per_model.values())
            cache_write = sum(m.cache_write_tokens for m in self._per_model.values())

        cost_unavailable = usage.total_cost <= 0.0 and not self.pricing_known()

        return UsageSummary(
            request_count=usage.request_count,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            context_tokens=usage.current_context_tokens,
            total_cost=usage.total_cost,
            cost_unavailable=cost_unavailable,
        )

    def save(self, path: Path) -> None:
        """
        Save usage data to JSON file.

        Args:
            path: Path to save usage data
        """
        # Ensure parent directories exist
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "session_usage": asdict(self.session_usage),
            "per_model": {model: asdict(usage) for model, usage in self._per_model.items()},
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path: Path) -> None:
        """
        Load usage data from JSON file.

        Args:
            path: Path to load usage data from
        """
        if not path.exists():
            return

        with open(path, "r") as f:
            data = json.load(f)

        # Load session usage
        if "session_usage" in data:
            self.session_usage = SessionUsage(**data["session_usage"])

        # Load per-model usage
        if "per_model" in data:
            self._per_model = {
                model: ModelUsage(**usage_data) for model, usage_data in data["per_model"].items()
            }

    def format_summary(self) -> str:
        """
        Format a human-readable usage summary.

        Returns:
            Formatted summary string with session totals and per-model breakdown.
            Includes a cache-read split when any cache tokens were recorded, and
            marks cost as unavailable (rather than ``$0.0000``) when per-token
            pricing is unknown.
        """
        summary = self.summary()
        lines = [
            "Usage Summary:",
            f"  Total Requests: {summary.request_count}",
            f"  Total Input Tokens: {summary.input_tokens:,}",
            f"  Total Output Tokens: {summary.output_tokens:,}",
        ]
        if summary.cache_read_tokens > 0:
            lines.append(f"  Cache Read Tokens: {summary.cache_read_tokens:,}")
        if summary.cache_write_tokens > 0:
            lines.append(f"  Cache Write Tokens: {summary.cache_write_tokens:,}")
        if summary.cost_unavailable:
            lines.append("  Total Cost: unavailable (pricing unknown for this model)")
        else:
            lines.append(f"  Total Cost: ${summary.total_cost:.4f}")

        if self._per_model:
            lines.append("\nPer-Model Breakdown:")
            for model, usage in sorted(self._per_model.items()):
                lines.append(f"\n  {model}:")
                lines.append(f"    Requests: {usage.request_count}")
                lines.append(f"    Input: {usage.input_tokens:,} tokens")
                lines.append(f"    Output: {usage.output_tokens:,} tokens")
                if usage.cache_read_tokens > 0:
                    lines.append(f"    Cache Read: {usage.cache_read_tokens:,} tokens")
                if usage.cache_write_tokens > 0:
                    lines.append(f"    Cache Write: {usage.cache_write_tokens:,} tokens")
                if summary.cost_unavailable:
                    lines.append("    Cost: unavailable")
                else:
                    lines.append(f"    Cost: ${usage.cost:.4f}")

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset session usage (for new session)."""
        self.session_usage = SessionUsage()
        self._model = None
        self._cached_costs = None
        self._per_model = {}
