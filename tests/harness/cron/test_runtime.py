"""Tests for draining fired cron prompts into the active scheduler."""

import asyncio

from koder_agent.harness.cron.runtime import CronPromptRunner
from koder_agent.harness.cron.storage import CronStorage


class _RecordingScheduler:
    def __init__(self, *, fail: bool = False, delay: float = 0):
        self.prompts: list[tuple[str, bool]] = []
        self.fail = fail
        self.delay = delay

    async def handle(self, prompt: str, render_output: bool = True) -> str:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("scheduled prompt failed")
        self.prompts.append((prompt, render_output))
        return prompt


async def _wait_until(assertion):
    deadline = asyncio.get_running_loop().time() + 1
    while True:
        if assertion():
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0.01)


def test_cron_prompt_runner_uses_current_scheduler(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    first_scheduler = _RecordingScheduler()
    current_scheduler = first_scheduler

    async def scenario():
        nonlocal current_scheduler
        runner = CronPromptRunner(
            lambda: current_scheduler,
            storage=storage,
            check_interval=60,
        )
        runner.start()
        try:
            runner.enqueue("first scheduled prompt")
            await _wait_until(lambda: first_scheduler.prompts)

            second_scheduler = _RecordingScheduler()
            current_scheduler = second_scheduler
            runner.enqueue("second scheduled prompt")
            await _wait_until(lambda: second_scheduler.prompts)

            assert first_scheduler.prompts == [("first scheduled prompt", True)]
            assert second_scheduler.prompts == [("second scheduled prompt", True)]
        finally:
            await runner.stop()

    asyncio.run(scenario())


def test_cron_prompt_runner_deletes_one_shot_after_success(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    job = storage.create(cron="* * * * *", prompt="once", recurring=False)
    scheduler = _RecordingScheduler()

    async def scenario():
        runner = CronPromptRunner(lambda: scheduler, storage=storage, check_interval=60)
        runner.start()
        try:
            runner.enqueue_job(job)
            await _wait_until(lambda: scheduler.prompts)

            assert storage.get(job["id"]) is None
        finally:
            await runner.stop()

    asyncio.run(scenario())


def test_cron_prompt_runner_keeps_one_shot_after_failure(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    job = storage.create(cron="* * * * *", prompt="once", recurring=False)
    scheduler = _RecordingScheduler(fail=True)

    async def scenario():
        runner = CronPromptRunner(lambda: scheduler, storage=storage, check_interval=60)
        runner.start()
        try:
            runner.enqueue_job(job)
            await _wait_until(lambda: job["id"] not in runner.pending_job_ids)

            assert storage.get(job["id"]) is not None
        finally:
            await runner.stop()

    asyncio.run(scenario())


def test_cron_prompt_runner_deduplicates_pending_job(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    job = storage.create(cron="* * * * *", prompt="slow", recurring=True)
    scheduler = _RecordingScheduler(delay=0.05)

    async def scenario():
        runner = CronPromptRunner(lambda: scheduler, storage=storage, check_interval=60)
        runner.start()
        try:
            runner.enqueue_job(job)
            runner.enqueue_job(job)
            await _wait_until(lambda: scheduler.prompts)

            assert scheduler.prompts == [("slow", True)]
        finally:
            await runner.stop()

    asyncio.run(scenario())


def test_cron_prompt_runner_survives_scheduler_getter_error(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    job = storage.create(cron="* * * * *", prompt="after getter failure", recurring=True)
    scheduler = _RecordingScheduler()
    calls = 0

    def get_scheduler():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("scheduler unavailable")
        return scheduler

    async def scenario():
        runner = CronPromptRunner(get_scheduler, storage=storage, check_interval=60)
        runner.start()
        try:
            runner.enqueue_job(job)
            await _wait_until(lambda: job["id"] not in runner.pending_job_ids)
            runner.enqueue_job(job)
            await _wait_until(lambda: scheduler.prompts)

            assert scheduler.prompts == [("after getter failure", True)]
        finally:
            await runner.stop()

    asyncio.run(scenario())
