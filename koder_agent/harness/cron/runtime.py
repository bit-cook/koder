"""Runtime bridge between durable cron jobs and the active agent scheduler."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable

from .scheduler import CronScheduler
from .storage import CronStorage, default_cron_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuedCronJob:
    id: str
    prompt: str
    recurring: bool


class CronPromptRunner:
    """Drain fired cron prompts into whichever scheduler is currently active."""

    def __init__(
        self,
        scheduler_getter: Callable[[], object],
        *,
        storage: CronStorage | None = None,
        check_interval: float = 60.0,
    ):
        if storage is None:
            storage = default_cron_storage()
        self._scheduler_getter = scheduler_getter
        self._storage = storage
        self._queue: asyncio.Queue[QueuedCronJob] = asyncio.Queue()
        self._pending_job_ids: set[str] = set()
        self._manual_counter = 0
        self._cron_scheduler = CronScheduler(
            storage,
            on_job_fire=self.enqueue_job,
            delete_one_shot_after_fire=False,
            check_interval=check_interval,
        )
        self._consumer_task: asyncio.Task | None = None

    @property
    def pending_job_ids(self) -> set[str]:
        return set(self._pending_job_ids)

    def enqueue(self, prompt: str) -> None:
        """Queue a fired cron prompt for the active scheduler."""

        self._manual_counter += 1
        self._queue.put_nowait(
            QueuedCronJob(
                id=f"manual:{self._manual_counter}",
                prompt=prompt,
                recurring=True,
            )
        )

    def enqueue_job(self, job: dict) -> bool:
        """Queue a stored cron job, skipping duplicates while it is pending/running."""

        job_id = str(job.get("id") or "")
        prompt = str(job.get("prompt") or "")
        if not job_id or not prompt:
            logger.warning("Cron job missing id or prompt: %s", job)
            return False
        if job_id in self._pending_job_ids:
            return False
        self._pending_job_ids.add(job_id)
        self._queue.put_nowait(
            QueuedCronJob(
                id=job_id,
                prompt=prompt,
                recurring=bool(job.get("recurring", True)),
            )
        )
        return True

    def start(self) -> None:
        """Start polling cron storage and consuming fired prompts."""

        if self._consumer_task is not None:
            return
        self._cron_scheduler.start()
        self._consumer_task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        """Stop background polling and prompt consumption."""

        await self._cron_scheduler.stop_async()

        if self._consumer_task is not None:
            self._consumer_task.cancel()
            await asyncio.gather(self._consumer_task, return_exceptions=True)
            self._consumer_task = None

    async def _consume(self) -> None:
        while True:
            queued = await self._queue.get()
            try:
                scheduler = self._scheduler_getter()
                if scheduler is None or not hasattr(scheduler, "handle"):
                    logger.warning("Cron prompt fired without an active scheduler")
                    continue
                await scheduler.handle(queued.prompt, render_output=True)
                if not queued.recurring:
                    self._storage.delete(queued.id)
            except Exception:
                logger.exception("Cron prompt failed: %s", queued.prompt[:80])
            finally:
                self._pending_job_ids.discard(queued.id)
