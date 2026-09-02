"""
database.py
===========
SQLite-backed state management for the Cinevo AI Social Media Engagement Bot.

Purpose:
    Persist every comment/post/thread that has already been replied to so the
    bot never replies to the same item twice, even across restarts.

Design:
    * A single lightweight SQLite database file (default: bot_state.db).
    * Thread-safe via a module-level lock and a per-call connection.
    * Two tables:
        - `replied_items` : deduplication of handled comments/posts.
        - `reply_log`     : audit trail of generated replies (optional but useful).
"""

import logging
import os
import sqlite3
import threading
import time
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

# A lock guarding all writes so concurrent platform workers stay safe.
_db_lock = threading.Lock()


class Database:
    """Thin wrapper around a SQLite connection with helper methods."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or settings.db_path
        # Ensure the parent directory exists.
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        """Open a new connection with sane pragmas."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_schema(self) -> None:
        """Create tables if they do not exist."""
        with _db_lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS replied_items (
                        platform    TEXT NOT NULL,
                        item_id     TEXT NOT NULL,
                        parent_id   TEXT,          -- e.g. video/post id
                        replied_at  INTEGER NOT NULL,
                        PRIMARY KEY (platform, item_id)
                    );

                    CREATE TABLE IF NOT EXISTS reply_log (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        platform    TEXT NOT NULL,
                        item_id     TEXT NOT NULL,
                        parent_id   TEXT,
                        comment     TEXT,
                        reply       TEXT,
                        created_at  INTEGER NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_replied_platform
                        ON replied_items (platform, replied_at);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------ #
    # Deduplication API
    # ------------------------------------------------------------------ #
    def is_replied(self, platform: str, item_id: str) -> bool:
        """Return True if the given comment/post has already been handled."""
        with _db_lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT 1 FROM replied_items WHERE platform=? AND item_id=?",
                    (platform, item_id),
                )
                return cur.fetchone() is not None
            finally:
                conn.close()

    def mark_replied(
        self,
        platform: str,
        item_id: str,
        parent_id: Optional[str] = None,
    ) -> None:
        """Record that an item has been replied to (idempotent)."""
        with _db_lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO replied_items "
                    "(platform, item_id, parent_id, replied_at) "
                    "VALUES (?, ?, ?, ?)",
                    (platform, item_id, parent_id, int(time.time())),
                )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------ #
    # Audit log API
    # ------------------------------------------------------------------ #
    def log_reply(
        self,
        platform: str,
        item_id: str,
        parent_id: Optional[str],
        comment: str,
        reply: str,
    ) -> None:
        """Append an entry to the audit log."""
        with _db_lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO reply_log "
                    "(platform, item_id, parent_id, comment, reply, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        platform,
                        item_id,
                        parent_id,
                        comment,
                        reply,
                        int(time.time()),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def recent_replies(self, limit: int = 20) -> list:
        """Return the most recent logged replies (for dashboards/debugging)."""
        with _db_lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT platform, item_id, parent_id, comment, reply, created_at "
                    "FROM reply_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
                return [dict(row) for row in cur.fetchall()]
            finally:
                conn.close()

    def count_replied(self, platform: Optional[str] = None) -> int:
        """Return how many items have been replied to (optionally per platform)."""
        with _db_lock:
            conn = self._connect()
            try:
                if platform:
                    cur = conn.execute(
                        "SELECT COUNT(*) AS c FROM replied_items WHERE platform=?",
                        (platform,),
                    )
                else:
                    cur = conn.execute("SELECT COUNT(*) AS c FROM replied_items")
                return int(cur.fetchone()["c"])
            finally:
                conn.close()


# A single shared instance used across the application.
db = Database()
