"""Basic cron scheduler that checks and fires jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable, Optional

from .storage import CronStorage

logger = logging.getLogger(__name__)


def _cron_matches_now(cron_expr: str, now: datetime) -> bool:
    """Check if a 5-field cron expression matches the given datetime.

    Fields: minute hour day-of-month month day-of-week
    Supports: * (any), specific values, comma-separated lists, ranges (1-5), steps (*/5)
    """
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False

    checks = [
        (fields[0], now.minute, 0, 59),  # minute
        (fields[1], now.hour, 0, 23),  # hour
        (fields[2], now.day, 1, 31),  # day of month
        (fields[3], now.month, 1, 12),  # month
    ]
    # Convert Python weekday (0=Mon) to cron weekday (0=Sun)
    cron_dow = (now.weekday() + 1) % 7
    checks.append((fields[4], cron_dow, 0, 7))

    for field_expr, current_value, min_val, max_val in checks:
        if not _field_matches(field_expr, current_value, min_val, max_val):
            return False
    return True


def _field_matches(expr: str, value: int, min_val: int, max_val: int) -> bool:
    """Check if a single cron field matches a value."""
    if expr == "*":
        return True

    for part in expr.split(","):
        # Handle step: */5 or 1-10/2
        if "/" in part:
            range_part, step = part.split("/", 1)
            step = int(step)
            if range_part == "*":
                if (value - min_val) % step == 0:
                    return True
            elif "-" in range_part:
                start, end = map(int, range_part.split("-", 1))
                if start <= value <= end and (value - start) % step == 0:
                    return True
        # Handle range: 1-5
        elif "-" in part:
            start, end = map(int, part.split("-", 1))
            if start <= value <= end:
                return True
        # Handle exact value
        else:
            if int(part) == value:
                return True

    return False


class CronScheduler:
    """Tick-based scheduler that fires cron jobs.

    Call `start()` to begin polling. Call `stop()` to shut down.
    The `on_fire` callback receives the prompt string when a job fires.
    """

    def __init__(
        self,
        storage: CronStorage,
        on_fire: Callable[[str], None],
        *,
        check_interval: float = 60.0,
    ):
        self._storage = storage
        self._on_fire = on_fire
        self._check_interval = check_interval
        self._task: Optional[asyncio.Task] = None
        self._stopped = False
        self._fired_this_minute: set[str] = set()
        self._last_minute: int = -1

    async def _tick(self) -> None:
        """Check all jobs and fire any that match current time."""
        now = datetime.now()

        # Reset fired set on minute change
        if now.minute != self._last_minute:
            self._fired_this_minute.clear()
            self._last_minute = now.minute

        jobs = self._storage.list_all()
        for job in jobs:
            if job["id"] in self._fired_this_minute:
                continue
            if _cron_matches_now(job["cron"], now):
                self._fired_this_minute.add(job["id"])
                logger.info("Firing cron job %s: %s", job["id"], job["prompt"][:50])
                try:
                    self._on_fire(job["prompt"])
                except Exception:
                    logger.exception("Error firing cron job %s", job["id"])
                # Delete one-shot jobs
                if not job.get("recurring", True):
                    self._storage.delete(job["id"])

    async def _loop(self) -> None:
        """Main scheduler loop."""
        while not self._stopped:
            try:
                await self._tick()
            except Exception:
                logger.exception("Cron scheduler tick error")
            await asyncio.sleep(self._check_interval)

    def start(self) -> None:
        """Start the scheduler in the background."""
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.ensure_future(self._loop())
        logger.info("Cron scheduler started (interval=%ss)", self._check_interval)

    def stop(self) -> None:
        """Stop the scheduler."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            self._task = None
        logger.info("Cron scheduler stopped")
