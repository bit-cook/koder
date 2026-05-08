"""Tests for cron job persistence."""

import pytest

from koder_agent.harness.cron.storage import CronStorage


def test_create_cron_job(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    job = storage.create(cron="0 9 * * *", prompt="daily standup", recurring=True)
    assert job["id"]
    assert job["cron"] == "0 9 * * *"
    assert job["prompt"] == "daily standup"
    assert job["recurring"] is True


def test_list_jobs(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    storage.create(cron="0 9 * * *", prompt="morning")
    storage.create(cron="0 17 * * *", prompt="evening")
    jobs = storage.list_all()
    assert len(jobs) == 2


def test_delete_job(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    job = storage.create(cron="0 9 * * *", prompt="temp")
    assert storage.delete(job["id"]) is True
    assert len(storage.list_all()) == 0


def test_delete_nonexistent(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    assert storage.delete("nonexistent") is False


def test_max_jobs_limit(tmp_path):
    storage = CronStorage(tmp_path / "crons.json", max_jobs=3)
    storage.create(cron="0 1 * * *", prompt="1")
    storage.create(cron="0 2 * * *", prompt="2")
    storage.create(cron="0 3 * * *", prompt="3")
    with pytest.raises(ValueError, match="limit"):
        storage.create(cron="0 4 * * *", prompt="4")


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "crons.json"
    s1 = CronStorage(path)
    s1.create(cron="0 9 * * *", prompt="persist me")
    s2 = CronStorage(path)
    jobs = s2.list_all()
    assert len(jobs) == 1
    assert jobs[0]["prompt"] == "persist me"
