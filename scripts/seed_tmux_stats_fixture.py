#!/usr/bin/env python3
"""Seed deterministic session stats for tmux feature scenarios."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SESSIONS = [
    (
        "stats-seed-a",
        "Stats Seed A",
        "1999-01-02 10:00:00",
        "1999-01-02 10:30:00",
        [
            {"role": "user", "content": "seed stats alpha"},
            {"role": "assistant", "content": "seed stats beta"},
        ],
    ),
    (
        "stats-seed-b",
        "Stats Seed B",
        "1999-01-03 11:00:00",
        "1999-01-03 11:15:00",
        [
            {"role": "user", "content": "seed stats gamma"},
            {"role": "assistant", "content": "seed stats delta"},
        ],
    ),
]


def main() -> int:
    db_path = Path.home() / ".koder" / "koder.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS agent_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES agent_sessions (session_id)
                    ON DELETE CASCADE
            )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS session_metadata (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                tag TEXT,
                color TEXT,
                agent TEXT,
                cwd TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

        for session_id, title, created_at, updated_at, messages in SESSIONS:
            conn.execute(
                """INSERT OR REPLACE INTO agent_sessions
                (session_id, created_at, updated_at) VALUES (?, ?, ?)""",
                (session_id, created_at, updated_at),
            )
            conn.execute(
                """INSERT OR REPLACE INTO session_metadata
                (session_id, title, cwd, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)""",
                (session_id, title, str(Path.cwd()), created_at, updated_at),
            )
            conn.execute("DELETE FROM agent_messages WHERE session_id = ?", (session_id,))
            for message in messages:
                conn.execute(
                    """INSERT INTO agent_messages
                    (session_id, message_data, created_at) VALUES (?, ?, ?)""",
                    (session_id, json.dumps(message), created_at),
                )
        conn.commit()

    marker = Path.cwd() / "stats-seed.txt"
    marker.write_text("seeded stats session rows\n", encoding="utf-8")
    print(f"stats-fixture: sessions={len(SESSIONS)} messages=4 db={db_path}")
    print(f"stats-fixture: marker={marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
