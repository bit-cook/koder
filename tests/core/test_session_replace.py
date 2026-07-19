"""Atomic replacement tests for :class:`EnhancedSQLiteSession`."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from contextlib import closing

import pytest

from koder_agent.core.session import EnhancedSQLiteSession


@pytest.fixture(autouse=True)
def _close_created_sessions(monkeypatch):
    created: list[EnhancedSQLiteSession] = []
    original_init = EnhancedSQLiteSession.__init__

    def tracked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(EnhancedSQLiteSession, "__init__", tracked_init)
    yield
    for session in created:
        session.close()


def _read_raw_items(db_path, session_id: str) -> list[dict]:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT message_data
            FROM agent_messages
            WHERE session_id = ?
            ORDER BY id ASC""",
            (session_id,),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _read_raw_message_data(db_path, session_id: str) -> list[str]:
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT message_data
            FROM agent_messages
            WHERE session_id = ?
            ORDER BY id ASC""",
            (session_id,),
        ).fetchall()
    return [row[0] for row in rows]


@pytest.mark.asyncio
async def test_replace_items_rolls_back_failure_without_visible_empty_window(tmp_path):
    db_path = tmp_path / "sessions.db"
    session = EnhancedSQLiteSession("atomic-failure", db_path=str(db_path))
    original = [
        {"role": "user", "content": "before", "metadata": {"turn": 1}},
        {"role": "assistant", "content": "answer", "timestamp": "2026-07-14T10:00:00Z"},
    ]
    await session.add_items(original)

    deleted = threading.Event()
    release = threading.Event()

    def fail_between_delete_and_insert(_conn):
        deleted.set()
        release.wait(timeout=5)
        raise RuntimeError("injected replacement failure")

    session._before_replace_insert = fail_between_delete_and_insert
    task = asyncio.create_task(session.replace_items([{"role": "user", "content": "replacement"}]))
    assert await asyncio.to_thread(deleted.wait, 5)

    assert await asyncio.to_thread(_read_raw_items, db_path, session.session_id) == original
    release.set()
    with pytest.raises(RuntimeError, match="injected replacement failure"):
        await task

    assert await session.get_items() == original


@pytest.mark.asyncio
async def test_replace_items_rolls_back_cancellation_without_visible_empty_window(tmp_path):
    db_path = tmp_path / "sessions.db"
    session = EnhancedSQLiteSession("atomic-cancel", db_path=str(db_path))
    original = [
        {"role": "user", "content": "before"},
        {"role": "assistant", "content": "answer"},
    ]
    await session.add_items(original)

    deleted = threading.Event()
    release = threading.Event()

    def pause_between_delete_and_insert(_conn):
        deleted.set()
        release.wait(timeout=5)

    session._before_replace_insert = pause_between_delete_and_insert
    task = asyncio.create_task(session.replace_items([{"role": "user", "content": "replacement"}]))
    assert await asyncio.to_thread(deleted.wait, 5)

    task.cancel()
    assert await asyncio.to_thread(_read_raw_items, db_path, session.session_id) == original
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await session.get_items() == original


@pytest.mark.asyncio
async def test_replace_items_waits_for_rollback_after_repeated_cancellation(tmp_path):
    db_path = tmp_path / "sessions.db"
    session = EnhancedSQLiteSession("atomic-double-cancel", db_path=str(db_path))
    original = [
        {"role": "user", "content": "before"},
        {"role": "assistant", "content": "answer"},
    ]
    await session.add_items(original)

    deleted = threading.Event()
    release = threading.Event()

    def pause_between_delete_and_insert(_conn):
        deleted.set()
        release.wait(timeout=5)

    session._before_replace_insert = pause_between_delete_and_insert
    before_tasks = set(asyncio.all_tasks())
    task = asyncio.create_task(session.replace_items([{"role": "user", "content": "replacement"}]))
    assert await asyncio.to_thread(deleted.wait, 5)

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert await asyncio.to_thread(_read_raw_items, db_path, session.session_id) == original

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await session.get_items() == original
    await session.replace_items([{"role": "user", "content": "after"}])
    assert await session.get_items() == [{"role": "user", "content": "after"}]
    await asyncio.sleep(0)
    leaked_tasks = [
        pending
        for pending in asyncio.all_tasks() - before_tasks
        if pending is not asyncio.current_task() and not pending.done()
    ]
    assert leaked_tasks == []


@pytest.mark.asyncio
async def test_replace_items_repeated_cancellation_before_commit_rolls_back(tmp_path):
    db_path = tmp_path / "sessions.db"
    session = EnhancedSQLiteSession("atomic-pre-commit-cancel", db_path=str(db_path))
    original = [{"role": "user", "content": "before"}]
    replacement = [{"role": "assistant", "content": "replacement"}]
    await session.add_items(original)

    before_commit = threading.Event()
    release = threading.Event()

    def pause_before_commit(_conn):
        before_commit.set()
        release.wait(timeout=5)

    session._before_replace_commit = pause_before_commit
    before_tasks = set(asyncio.all_tasks())
    task = asyncio.create_task(session.replace_items(replacement))
    assert await asyncio.to_thread(before_commit.wait, 5)

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await session.get_items() == original
    await session.replace_items([{"role": "user", "content": "after"}])
    assert await session.get_items() == [{"role": "user", "content": "after"}]
    await asyncio.sleep(0)
    leaked_tasks = [
        pending
        for pending in asyncio.all_tasks() - before_tasks
        if pending is not asyncio.current_task() and not pending.done()
    ]
    assert leaked_tasks == []


@pytest.mark.asyncio
async def test_replace_items_repeated_cancellation_after_commit_wins_reports_success(tmp_path):
    db_path = tmp_path / "sessions.db"
    session = EnhancedSQLiteSession("atomic-commit-wins", db_path=str(db_path))
    original = [{"role": "user", "content": "before"}]
    replacement = [{"role": "assistant", "content": "replacement"}]
    await session.add_items(original)

    commit_started = threading.Event()
    release = threading.Event()

    def pause_during_commit(conn):
        commit_started.set()
        release.wait(timeout=5)
        conn.commit()

    session._commit_replace_transaction = pause_during_commit
    before_tasks = set(asyncio.all_tasks())
    task = asyncio.create_task(session.replace_items(replacement))
    assert await asyncio.to_thread(commit_started.wait, 5)

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release.set()
    await task

    assert await session.get_items() == replacement
    await session.replace_items([{"role": "user", "content": "after"}])
    assert await session.get_items() == [{"role": "user", "content": "after"}]
    await asyncio.sleep(0)
    leaked_tasks = [
        pending
        for pending in asyncio.all_tasks() - before_tasks
        if pending is not asyncio.current_task() and not pending.done()
    ]
    assert leaked_tasks == []


@pytest.mark.asyncio
async def test_replace_items_commits_exact_order_and_preserves_session_metadata(tmp_path):
    db_path = tmp_path / "sessions.db"
    session = EnhancedSQLiteSession("atomic-success", db_path=str(db_path))
    retained = {
        "role": "assistant",
        "content": "retained",
        "timestamp": "2026-07-14T10:00:00Z",
        "metadata": {"source": "sdk"},
    }
    await session.add_items([{"role": "user", "content": "old"}, retained])

    with closing(sqlite3.connect(db_path)) as conn:
        session_created_at = conn.execute(
            "SELECT created_at FROM agent_sessions WHERE session_id = ?",
            (session.session_id,),
        ).fetchone()[0]
        retained_created_at = conn.execute(
            """SELECT created_at FROM agent_messages
            WHERE session_id = ? ORDER BY id ASC LIMIT 1 OFFSET 1""",
            (session.session_id,),
        ).fetchone()[0]

    replacement = [
        retained,
        {"role": "user", "content": "new", "metadata": {"turn": 2}},
        {"role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
    ]
    await session.replace_items(replacement)

    assert await session.get_items() == replacement
    with closing(sqlite3.connect(db_path)) as conn:
        assert (
            conn.execute(
                "SELECT created_at FROM agent_sessions WHERE session_id = ?",
                (session.session_id,),
            ).fetchone()[0]
            == session_created_at
        )
        assert (
            conn.execute(
                """SELECT created_at FROM agent_messages
                WHERE session_id = ? ORDER BY id ASC LIMIT 1""",
                (session.session_id,),
            ).fetchone()[0]
            == retained_created_at
        )


@pytest.mark.asyncio
async def test_replace_items_restores_snapshot_byte_for_byte_under_tiny_micro_limit(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KODER_MICRO_COMPACT", "1")
    monkeypatch.setenv("KODER_MICRO_COMPACT_MAX_CHARS", "10")
    db_path = tmp_path / "sessions.db"
    session = EnhancedSQLiteSession("exact-snapshot", db_path=str(db_path))
    snapshot = [
        {
            "type": "function_call_output",
            "call_id": "call-long",
            "output": "0123456789" * 20,
            "metadata": {"nested": ["preserve", {"order": 2}]},
        },
        {"role": "assistant", "content": "tail", "metadata": {"turn": 7}},
    ]
    expected_bytes = [json.dumps(item) for item in snapshot]

    await session.replace_items(snapshot)
    assert await session.get_items() == snapshot
    assert _read_raw_message_data(db_path, session.session_id) == expected_bytes

    await session.replace_items([{"role": "user", "content": "temporary"}])
    await session.replace_items(snapshot)

    assert await session.get_items() == snapshot
    assert _read_raw_message_data(db_path, session.session_id) == expected_bytes


@pytest.mark.asyncio
async def test_replace_items_surfaces_rollback_failure_and_retains_original(tmp_path):
    db_path = tmp_path / "sessions.db"
    session = EnhancedSQLiteSession("rollback-failure", db_path=str(db_path))
    original = [{"role": "user", "content": "before"}]
    await session.add_items(original)

    def fail_after_delete(_conn):
        raise ValueError("write failed")

    def fail_rollback(_conn):
        raise RuntimeError("rollback failed")

    session._before_replace_insert = fail_after_delete
    session._rollback_replace_transaction = fail_rollback

    with pytest.raises(RuntimeError, match="rollback failed"):
        await session.replace_items([{"role": "assistant", "content": "after"}])

    assert await session.get_items() == original


@pytest.mark.asyncio
async def test_concurrent_replace_items_serialize_without_mixed_history(tmp_path):
    db_path = tmp_path / "sessions.db"
    first_session = EnhancedSQLiteSession("concurrent-replace", db_path=str(db_path))
    second_session = EnhancedSQLiteSession("concurrent-replace", db_path=str(db_path))
    original = [{"role": "user", "content": "original"}]
    first_replacement = [
        {"role": "assistant", "content": "first-a"},
        {"role": "assistant", "content": "first-b"},
    ]
    second_replacement = [
        {"role": "user", "content": "second-a"},
        {"role": "assistant", "content": "second-b"},
    ]
    await first_session.add_items(original)

    first_open = threading.Event()
    release_first = threading.Event()
    second_acquired = threading.Event()

    def pause_first(_conn):
        first_open.set()
        release_first.wait(timeout=5)

    def observe_second(_conn):
        second_acquired.set()

    first_session._before_replace_insert = pause_first
    second_session._before_replace_insert = observe_second

    first_task = asyncio.create_task(first_session.replace_items(first_replacement))
    assert await asyncio.to_thread(first_open.wait, 5)
    assert _read_raw_items(db_path, first_session.session_id) == original

    second_task = asyncio.create_task(second_session.replace_items(second_replacement))
    await asyncio.sleep(0.05)
    assert not second_acquired.is_set()
    assert _read_raw_items(db_path, first_session.session_id) == original

    release_first.set()
    await first_task
    await second_task

    assert second_acquired.is_set()
    assert await first_session.get_items() == second_replacement
