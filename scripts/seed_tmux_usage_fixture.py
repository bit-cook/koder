#!/usr/bin/env python3
"""Seed deterministic usage snapshots for tmux feature scenarios."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from koder_agent.core.usage_tracker import UsageTracker, usage_snapshot_path


def _current_session_id() -> str:
    db = Path.home() / ".koder" / "koder.db"
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "select session_id from session_metadata order by updated_at desc limit 1"
        ).fetchone()
    if row is None:
        raise RuntimeError("no active session metadata found")
    return str(row[0])


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "known"
    session_id = _current_session_id()
    tracker = UsageTracker()
    if mode == "known":
        tracker._model = "gpt-4.1"
        tracker.record_usage(1000, 2000, context_tokens=4096, model="gpt-4.1")
        tracker.record_usage(500, 100, context_tokens=4900, model="gpt-4.1")
    elif mode == "unknown":
        model = "totally-unknown-model-xyz-99999"
        tracker._model = model
        tracker.record_usage(100, 50, context_tokens=150, model=model)
    else:
        raise SystemExit(f"unknown usage fixture mode: {mode}")

    path = usage_snapshot_path(session_id)
    tracker.save(path)
    marker = f"{mode}-usage-fixture"
    Path(f"{mode}-usage-seed.txt").write_text(
        f"seeded {mode} usage snapshot\nsession_id={session_id}\npath={path}\n",
        encoding="utf-8",
    )
    print(marker, session_id, path)


if __name__ == "__main__":
    main()
