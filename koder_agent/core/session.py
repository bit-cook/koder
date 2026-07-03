"""Enhanced SQLiteSession with Koder-specific features.

This module extends the official agents.SQLiteSession to add:
- Session titles with LLM-based generation
- Token estimation helpers
- Separate metadata storage for extensibility

Conversation compaction is intentionally NOT handled here; it is owned by the
scheduler so there is a single, modern compaction path.
"""

import json
import os
from collections import Counter
from typing import Dict, List, Optional

import aiosqlite
import tiktoken
from agents import SQLiteSession
from agents.items import TResponseInputItem

from ..utils.client import llm_completion


async def migrate_legacy_sessions(db_path: str) -> int:
    """Migrate legacy sessions from ctx table to new SQLiteSession format.

    This function:
    1. Checks if the old `ctx` table exists
    2. Checks if migration has already been performed
    3. Migrates all sessions and titles to the new format
    4. Marks migration as complete

    The old `ctx` table is kept for a grace period as backup.

    Args:
        db_path: Path to the SQLite database

    Returns:
        Number of legacy sessions migrated during this call.
    """
    async with aiosqlite.connect(db_path) as conn:
        # Check if legacy ctx table exists
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ctx'"
        )
        if not await cursor.fetchone():
            return 0  # No legacy data to migrate

        # Check if migration already done
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='migration_status'"
        )
        if await cursor.fetchone():
            return 0  # Already migrated

        # Create session_metadata table if not exists
        await conn.execute("""CREATE TABLE IF NOT EXISTS session_metadata (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                tag TEXT,
                color TEXT,
                agent TEXT,
                cwd TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")

        # Get all legacy sessions
        cursor = await conn.execute("SELECT sid, msgs, title FROM ctx")
        sessions = await cursor.fetchall()

        # First, close this connection to avoid locks
        await conn.commit()

    # Now migrate each session with separate connections
    migrated_count = 0
    for session_id, msgs_json, title in sessions:
        try:
            # Parse messages
            messages = json.loads(msgs_json) if msgs_json else []

            if messages:
                # Create SQLiteSession instance and add items
                # This will open its own connection
                session = SQLiteSession(session_id, db_path)
                await session.add_items(messages)

            # Migrate title to session_metadata table (separate connection)
            if title:
                async with aiosqlite.connect(db_path) as title_conn:
                    await title_conn.execute(
                        """INSERT OR REPLACE INTO session_metadata
                        (session_id, title) VALUES (?, ?)""",
                        (session_id, title),
                    )
                    await title_conn.commit()

            migrated_count += 1

        except Exception:
            continue

    # Mark migration as complete (separate connection)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""CREATE TABLE migration_status (
                migrated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                migrated_sessions INTEGER
            )""")
        await conn.execute(
            "INSERT INTO migration_status (migrated_sessions) VALUES (?)", (migrated_count,)
        )
        await conn.commit()
    return migrated_count


class EnhancedSQLiteSession(SQLiteSession):
    """Extended SQLiteSession with title and metadata management.

    This class wraps the official SQLiteSession and adds:
    1. Session titles stored in a separate metadata table
    2. LLM-based title generation from first user message
    3. Token estimation helpers used by the scheduler

    The session itself is a pure storage layer. Conversation compaction is
    owned entirely by the scheduler (``AutoCompactManager`` +
    ``llm_compact_messages``); ``add_items`` no longer performs any
    summarization.
    """

    def __init__(
        self,
        session_id: str,
        db_path: Optional[str] = None,
        summarization_threshold: Optional[int] = None,
    ):
        """Initialize enhanced session.

        Args:
            session_id: Unique identifier for this session
            db_path: Path to SQLite database file (default: ~/.koder/koder.db)
            summarization_threshold: Deprecated/inert. Kept only for backwards
                                   compatibility with callers that still set it
                                   (e.g. the scheduler's legacy suppression
                                   hack). It no longer triggers any behavior.
        """
        # Set up database path
        if db_path is None:
            home_dir = os.path.expanduser("~")
            koder_dir = os.path.join(home_dir, ".koder")
            os.makedirs(koder_dir, exist_ok=True)
            db_path = os.path.join(koder_dir, "koder.db")

        # Initialize base SQLiteSession
        super().__init__(session_id, db_path)

        # Inert attribute kept for backwards compatibility (see docstring).
        self.summarization_threshold = summarization_threshold
        self._title: Optional[str] = None
        self._tag: Optional[str] = None
        self._color: Optional[str] = None

        # Initialize tiktoken encoder for accurate token counting
        try:
            self.encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            try:
                self.encoder = tiktoken.encoding_for_model("gpt-4o")
            except Exception:
                # Fallback: approximate tokens using UTF-8 bytes
                class _NaiveEncoder:
                    def encode(self, text: str) -> list[int]:
                        return list(text.encode("utf-8"))

                self.encoder = _NaiveEncoder()

    async def _ensure_metadata_table(self) -> None:
        """Ensure the session_metadata table exists."""
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("""CREATE TABLE IF NOT EXISTS session_metadata (
                    session_id TEXT PRIMARY KEY,
                    title TEXT,
                    tag TEXT,
                    color TEXT,
                    agent TEXT,
                    cwd TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
            cursor = await conn.execute("PRAGMA table_info(session_metadata)")
            columns = {row[1] for row in await cursor.fetchall()}
            if "cwd" not in columns:
                await conn.execute("ALTER TABLE session_metadata ADD COLUMN cwd TEXT")
            if "agent" not in columns:
                await conn.execute("ALTER TABLE session_metadata ADD COLUMN agent TEXT")
            if "tag" not in columns:
                await conn.execute("ALTER TABLE session_metadata ADD COLUMN tag TEXT")
            if "color" not in columns:
                await conn.execute("ALTER TABLE session_metadata ADD COLUMN color TEXT")
            await conn.commit()

    @classmethod
    async def collect_local_stats(cls, db_path: Optional[str] = None) -> dict[str, object]:
        resolved_db_path = cls._resolve_db_path(db_path)
        session = cls(session_id="stats-probe", db_path=resolved_db_path)
        await session._ensure_metadata_table()

        summary = {
            "total_sessions": 0,
            "total_messages": 0,
            "active_days": 0,
            "first_session_date": None,
            "last_session_date": None,
            "peak_day": None,
        }

        try:
            async with aiosqlite.connect(resolved_db_path) as conn:
                cursor = await conn.execute("""
                    SELECT session_id, created_at, updated_at
                    FROM session_metadata
                    ORDER BY created_at ASC, session_id ASC
                    """)
                rows = await cursor.fetchall()
                if rows:
                    total_sessions = len(rows)
                    created_dates = [str(row[1])[:10] for row in rows if row[1]]
                    updated_dates = [str(row[2])[:10] for row in rows if row[2]]
                    all_dates = [*created_dates, *updated_dates]
                    peak_day = None
                    if created_dates:
                        counts = Counter(created_dates)
                        peak_day = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][
                            0
                        ]
                    summary.update(
                        {
                            "total_sessions": total_sessions,
                            "active_days": len(set(all_dates)),
                            "first_session_date": min(created_dates) if created_dates else None,
                            "last_session_date": (
                                max(updated_dates or created_dates)
                                if (updated_dates or created_dates)
                                else None
                            ),
                            "peak_day": peak_day,
                        }
                    )

                for table_name in ("agent_messages", "items"):
                    cursor = await conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
                        (table_name,),
                    )
                    if await cursor.fetchone():
                        cursor = await conn.execute(f"SELECT COUNT(*) FROM {table_name}")
                        row = await cursor.fetchone()
                        summary["total_messages"] = int(row[0]) if row and row[0] is not None else 0
                        break
        except Exception:
            return summary

        return summary

    @staticmethod
    def _resolve_db_path(db_path: Optional[str] = None) -> str:
        if db_path is not None:
            return db_path
        home_dir = os.path.expanduser("~")
        os.makedirs(os.path.join(home_dir, ".koder"), exist_ok=True)
        return os.path.join(home_dir, ".koder", "koder.db")

    @classmethod
    async def record_session_cwd(
        cls,
        session_id: str,
        cwd: str,
        db_path: Optional[str] = None,
    ) -> None:
        resolved_db_path = cls._resolve_db_path(db_path)
        session = cls(session_id=session_id, db_path=resolved_db_path)
        await session._ensure_metadata_table()
        async with aiosqlite.connect(resolved_db_path) as conn:
            await conn.execute(
                """INSERT INTO session_metadata (session_id, cwd, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    cwd = excluded.cwd,
                    updated_at = CURRENT_TIMESTAMP""",
                (session_id, cwd),
            )
            await conn.commit()

    @classmethod
    async def get_most_recent_session_for_cwd(
        cls,
        cwd: str,
        db_path: Optional[str] = None,
    ) -> Optional[str]:
        resolved_db_path = cls._resolve_db_path(db_path)
        session = cls(session_id="metadata-probe", db_path=resolved_db_path)
        await session._ensure_metadata_table()
        try:
            async with aiosqlite.connect(resolved_db_path) as conn:
                cursor = await conn.execute(
                    """SELECT session_id
                    FROM session_metadata
                    WHERE cwd = ?
                    ORDER BY updated_at DESC, created_at DESC, session_id DESC
                    LIMIT 1""",
                    (cwd,),
                )
                row = await cursor.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    @classmethod
    async def record_session_agent(
        cls,
        session_id: str,
        agent: str,
        db_path: Optional[str] = None,
    ) -> None:
        resolved_db_path = cls._resolve_db_path(db_path)
        session = cls(session_id=session_id, db_path=resolved_db_path)
        await session._ensure_metadata_table()
        async with aiosqlite.connect(resolved_db_path) as conn:
            await conn.execute(
                """INSERT INTO session_metadata (session_id, agent, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    agent = excluded.agent,
                    updated_at = CURRENT_TIMESTAMP""",
                (session_id, agent),
            )
            await conn.commit()

    @classmethod
    async def get_session_agent(
        cls,
        session_id: str,
        db_path: Optional[str] = None,
    ) -> Optional[str]:
        resolved_db_path = cls._resolve_db_path(db_path)
        session = cls(session_id="metadata-probe", db_path=resolved_db_path)
        await session._ensure_metadata_table()
        try:
            async with aiosqlite.connect(resolved_db_path) as conn:
                cursor = await conn.execute(
                    "SELECT agent FROM session_metadata WHERE session_id = ?",
                    (session_id,),
                )
                row = await cursor.fetchone()
                return row[0] if row and row[0] else None
        except Exception:
            return None

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        """Persist items to the session storage.

        This session is a pure storage layer: compaction is owned entirely by
        the scheduler's ``AutoCompactManager`` + ``llm_compact_messages`` path.
        The legacy per-user-turn summarizer that previously lived here has been
        removed (it competed with the scheduler's modern path and used bare
        ``print()`` which corrupted the Rich Live UI). The
        ``summarization_threshold`` attribute is kept for backwards
        compatibility with callers that still set it, but it is now inert.

        Args:
            items: List of message dictionaries to add
        """
        if not items:
            await super().add_items(items)
            return

        await super().add_items(items)

    def _estimate_tokens(self, messages: List[Dict]) -> int:
        """Accurately calculate token count for message history.

        Args:
            messages: List of message dictionaries

        Returns:
            Estimated total token count
        """
        total_tokens = 0

        for msg in messages:
            if not isinstance(msg, dict):
                total_tokens += len(self.encoder.encode(str(msg)))
                continue

            content = msg.get("content", "")
            if isinstance(content, str):
                total_tokens += len(self.encoder.encode(content))
            elif isinstance(content, list):
                # Handle multimodal messages (e.g., images + text)
                for block in content:
                    if isinstance(block, dict):
                        total_tokens += len(
                            self.encoder.encode(json.dumps(block, ensure_ascii=False))
                        )
                    else:
                        total_tokens += len(self.encoder.encode(str(block)))
            else:
                total_tokens += len(self.encoder.encode(str(content)))

            # Metadata overhead per message (~4 tokens)
            total_tokens += 4

        return total_tokens

    async def get_title(self) -> Optional[str]:
        """Get the title for this session.

        Returns:
            Session title or None if not set
        """
        try:
            await self._ensure_metadata_table()
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT title FROM session_metadata WHERE session_id = ?", (self.session_id,)
                )
                row = await cursor.fetchone()
                return row[0] if row and row[0] else None
        except Exception:
            return None

    async def set_title(self, title: str) -> None:
        """Set the title for this session.

        Args:
            title: Title to set
        """
        try:
            await self._ensure_metadata_table()
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    """INSERT INTO session_metadata (session_id, title, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(session_id) DO UPDATE SET
                        title = excluded.title,
                        updated_at = CURRENT_TIMESTAMP""",
                    (self.session_id, title),
                )
                await conn.commit()
        except Exception as e:
            print(f"[Session] Error setting title: {e}")

    async def get_tag(self) -> Optional[str]:
        """Get the tag for this session."""
        try:
            await self._ensure_metadata_table()
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT tag FROM session_metadata WHERE session_id = ?",
                    (self.session_id,),
                )
                row = await cursor.fetchone()
                tag = row[0] if row and row[0] else None
                self._tag = tag
                return tag
        except Exception:
            return self._tag

    async def set_tag(self, tag: Optional[str]) -> None:
        """Set or clear the tag for this session."""
        try:
            await self._ensure_metadata_table()
            normalized = tag.strip() if tag and tag.strip() else None
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    """INSERT INTO session_metadata (session_id, tag, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(session_id) DO UPDATE SET
                        tag = excluded.tag,
                        updated_at = CURRENT_TIMESTAMP""",
                    (self.session_id, normalized),
                )
                await conn.commit()
            self._tag = normalized
        except Exception as e:
            print(f"[Session] Error setting tag: {e}")

    async def get_color(self) -> Optional[str]:
        """Get the display color for this session."""
        try:
            await self._ensure_metadata_table()
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT color FROM session_metadata WHERE session_id = ?",
                    (self.session_id,),
                )
                row = await cursor.fetchone()
                color = row[0] if row and row[0] else None
                self._color = color
                return color
        except Exception:
            return self._color

    async def set_color(self, color: Optional[str]) -> None:
        """Set or clear the display color for this session."""
        try:
            await self._ensure_metadata_table()
            normalized = color.strip() if color and color.strip() else None
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    """INSERT INTO session_metadata (session_id, color, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(session_id) DO UPDATE SET
                        color = excluded.color,
                        updated_at = CURRENT_TIMESTAMP""",
                    (self.session_id, normalized),
                )
                await conn.commit()
            self._color = normalized
        except Exception as e:
            print(f"[Session] Error setting color: {e}")

    async def get_cwd(self) -> Optional[str]:
        """Get the recorded working directory for this session."""
        try:
            await self._ensure_metadata_table()
            async with aiosqlite.connect(self.db_path) as conn:
                cursor = await conn.execute(
                    "SELECT cwd FROM session_metadata WHERE session_id = ?",
                    (self.session_id,),
                )
                row = await cursor.fetchone()
                return row[0] if row and row[0] else None
        except Exception:
            return None

    async def generate_title(self, user_message: str) -> str:
        """Generate a concise session title from the first user message.

        Uses LLM to create a 3-6 word title that captures the main intent.

        Args:
            user_message: First user message in the session

        Returns:
            Generated title (fallback to truncated message if generation fails)
        """
        if not user_message or not user_message.strip():
            return "New session"

        prompt = f"""Generate a very short, descriptive title (3-6 words max) for this coding session based on the user's first message. The title should capture the main intent or task. Only return the title text, nothing else.

User message: {user_message[:500]}

Examples of good titles:
- Fix authentication bug
- Add dark mode toggle
- Refactor database queries
- Setup CI/CD pipeline"""

        try:
            title = await llm_completion(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a concise title generator. Return only the title, no quotes or extra text.",
                    },
                    {"role": "user", "content": prompt},
                ],
                use_small=True,
            )
            # Clean up the title
            title = title.strip().strip("\"'").strip()
            # Limit length
            if len(title) > 50:
                title = title[:47] + "..."
            return title
        except Exception:
            # Fallback: use first 50 chars of user message
            fallback = user_message[:50].strip()
            if len(user_message) > 50:
                fallback += "..."
            return fallback

    async def get_display_name(self) -> str:
        """Get display name for session: 'title - YYYY-MM-DD HH:MM' or session ID.

        Returns:
            Formatted display name
        """
        title = await self.get_title()
        if title:
            from ..utils.sessions import parse_session_dt

            _, dt = parse_session_dt(self.session_id)
            if dt:
                date_suffix = dt.strftime("%Y-%m-%d %H:%M")
                return f"{title} - {date_suffix}"
            return title
        return self.session_id

    @staticmethod
    async def list_sessions_with_titles(
        db_path: Optional[str] = None,
    ) -> list[tuple[str, Optional[str]]]:
        """List all sessions with their titles from the database.

        This is a static method that queries all sessions, not just the current one.

        Args:
            db_path: Path to database (default: ~/.koder/koder.db)

        Returns:
            List of (session_id, title) tuples
        """
        if db_path is None:
            home_dir = os.path.expanduser("~")
            db_path = os.path.join(home_dir, ".koder", "koder.db")

        try:
            async with aiosqlite.connect(db_path) as conn:
                # Ensure metadata table exists
                await conn.execute("""CREATE TABLE IF NOT EXISTS session_metadata (
                        session_id TEXT PRIMARY KEY,
                        title TEXT,
                        tag TEXT,
                        color TEXT,
                        cwd TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )""")
                cursor = await conn.execute("PRAGMA table_info(session_metadata)")
                columns = {row[1] for row in await cursor.fetchall()}
                if "cwd" not in columns:
                    await conn.execute("ALTER TABLE session_metadata ADD COLUMN cwd TEXT")
                if "tag" not in columns:
                    await conn.execute("ALTER TABLE session_metadata ADD COLUMN tag TEXT")
                if "color" not in columns:
                    await conn.execute("ALTER TABLE session_metadata ADD COLUMN color TEXT")

                # Get all sessions from the SQLiteSession table
                # SQLiteSession stores data in an 'items' table
                session_ids = set()

                # Check if there's an 'items' table (SQLiteSession's storage table)
                cursor = await conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
                )
                if await cursor.fetchone():
                    cursor = await conn.execute("SELECT DISTINCT session_id FROM items")
                    rows = await cursor.fetchall()
                    session_ids.update(row[0] for row in rows)

                # Also get sessions from metadata table
                cursor = await conn.execute("SELECT session_id FROM session_metadata")
                rows = await cursor.fetchall()
                session_ids.update(row[0] for row in rows)

                # Get titles for all sessions
                result = []
                for session_id in session_ids:
                    cursor = await conn.execute(
                        "SELECT title FROM session_metadata WHERE session_id = ?", (session_id,)
                    )
                    row = await cursor.fetchone()
                    title = row[0] if row and row[0] else None
                    result.append((session_id, title))

                return result

        except Exception as e:
            print(f"[Session] Error listing sessions: {e}")
            return []
