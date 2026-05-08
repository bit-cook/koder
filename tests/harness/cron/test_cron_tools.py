"""Tests for CronCreate, CronDelete, CronList tools."""

import json

import pytest

from koder_agent.harness.cron.storage import CronStorage
from koder_agent.tools.cron import (
    _set_cron_storage,
    cron_create,
    cron_delete,
    cron_list,
)


@pytest.fixture(autouse=True)
def _fresh_storage(tmp_path):
    storage = CronStorage(tmp_path / "crons.json")
    _set_cron_storage(storage)
    yield
    _set_cron_storage(None)


def test_cron_create():
    result = json.loads(cron_create(cron="0 9 * * *", prompt="standup"))
    assert result["id"]
    assert result["recurring"] is True


def test_cron_create_one_shot():
    result = json.loads(cron_create(cron="30 14 * * 1", prompt="monday meeting", recurring=False))
    assert result["recurring"] is False


def test_cron_create_invalid_expression():
    result = json.loads(cron_create(cron="invalid", prompt="bad"))
    assert "error" in result


def test_cron_list_empty():
    result = json.loads(cron_list())
    assert result["jobs"] == []


def test_cron_list_with_jobs():
    cron_create(cron="0 9 * * *", prompt="morning")
    cron_create(cron="0 17 * * *", prompt="evening")
    result = json.loads(cron_list())
    assert len(result["jobs"]) == 2


def test_cron_delete_existing():
    created = json.loads(cron_create(cron="0 9 * * *", prompt="temp"))
    result = json.loads(cron_delete(id=created["id"]))
    assert result["id"] == created["id"]
    assert len(json.loads(cron_list())["jobs"]) == 0


def test_cron_delete_nonexistent():
    result = json.loads(cron_delete(id="nonexistent"))
    assert "error" in result or "not found" in result.get("message", "").lower()
