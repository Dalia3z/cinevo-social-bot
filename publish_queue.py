"""
publish_queue.py
================
Local content queue for TikTok / YouTube Shorts packages.

WHY A SEPARATE MODULE?
----------------------
The engagement bot's `database.py` tracks which COMMENTS were replied to.
This module tracks generated VIDEO CONTENT. Mixing them would be confusing
and risky, so we use a SEPARATE SQLite file (`content_queue.db`) with its
own schema. Nothing here can affect the reply bot.

LIFECYCLE
---------
    draft  -> generated, awaiting human review
    ready  -> approved, ready to film/post
    posted -> published (with platform + URL recorded)
    skipped-> rejected by the human

This module NEVER posts anything. It only stores and retrieves text.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

# Separate DB file - deliberately NOT bot_state.db.
DEFAULT_QUEUE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "content_queue.db"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS content (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    language     TEXT NOT NULL DEFAULT '',
    hook         TEXT NOT NULL DEFAULT '',
    script       TEXT NOT NULL DEFAULT '',
    on_screen    TEXT NOT NULL DEFAULT '[]',
    caption      TEXT NOT NULL DEFAULT '',
    hashtags     TEXT NOT NULL DEFAULT '[]',
    cta          TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'draft',
    platform     TEXT NOT NULL DEFAULT '',
    posted_url   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_content_status ON content(status);
CREATE INDEX IF NOT EXISTS idx_content_title  ON content(title);
"""


class PublishQueue:
    """SQLite-backed queue of generated short-video content packages."""

    def __init__(self, path: str = "") -> None:
        self.path = path or os.getenv("CONTENT_QUEUE_DB", DEFAULT_QUEUE_PATH)
        self._init_db()

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            logger.error("Failed to initialise content queue at %s: %s", self.path, exc)

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def add(self, package: Dict, status: str = "draft") -> Optional[int]:
        """
        Insert a generated package. Returns the new row id, or None on error.
        Skips duplicates: if the same title already exists, returns its id.
        """
        title = (package.get("title") or "").strip()
        if not title:
            logger.error("Cannot queue a package without a title.")
            return None

        existing = self.find_by_title(title)
        if existing:
            logger.info("Content for '%s' already queued (id=%s).", title, existing["id"])
            return existing["id"]

        now = _now()
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO content
                        (title, language, hook, script, on_screen, caption,
                         hashtags, cta, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        package.get("language", ""),
                        package.get("hook", ""),
                        package.get("script", ""),
                        json.dumps(package.get("on_screen", []), ensure_ascii=False),
                        package.get("caption", ""),
                        json.dumps(package.get("hashtags", []), ensure_ascii=False),
                        package.get("cta", ""),
                        status,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)
        except sqlite3.Error as exc:
            logger.error("Failed to add content '%s': %s", title, exc)
            return None

    def set_status(
        self,
        content_id: int,
        status: str,
        platform: str = "",
        posted_url: str = "",
    ) -> bool:
        """Update the status (and optionally platform/url) of a row."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE content
                       SET status = ?, platform = ?, posted_url = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (status, platform, posted_url, _now(), content_id),
                )
            return True
        except sqlite3.Error as exc:
            logger.error("Failed to update content %s: %s", content_id, exc)
            return False

    def delete(self, content_id: int) -> bool:
        """Remove a row entirely."""
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM content WHERE id = ?", (content_id,))
            return True
        except sqlite3.Error as exc:
            logger.error("Failed to delete content %s: %s", content_id, exc)
            return False

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        d = dict(row)
        d["on_screen"] = _safe_json_list(d.get("on_screen"))
        d["hashtags"] = _safe_json_list(d.get("hashtags"))
        return d

    def find_by_title(self, title: str) -> Optional[Dict]:
        """Return the most recent row for a title (case-insensitive)."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM content WHERE LOWER(title) = LOWER(?) "
                    "ORDER BY id DESC LIMIT 1",
                    (title.strip(),),
                ).fetchone()
            return self._row_to_dict(row) if row else None
        except sqlite3.Error as exc:
            logger.error("Failed to look up '%s': %s", title, exc)
            return None

    def list(self, status: str = "", limit: int = 50) -> List[Dict]:
        """List rows, optionally filtered by status."""
        try:
            with self._connect() as conn:
                if status:
                    rows = conn.execute(
                        "SELECT * FROM content WHERE status = ? "
                        "ORDER BY id DESC LIMIT ?",
                        (status, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM content ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except sqlite3.Error as exc:
            logger.error("Failed to list content: %s", exc)
            return []

    def stats(self) -> Dict[str, int]:
        """Count rows per status (for a quick dashboard)."""
        out: Dict[str, int] = {}
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM content GROUP BY status"
                ).fetchall()
            for r in rows:
                out[r["status"]] = r["n"]
        except sqlite3.Error as exc:
            logger.error("Failed to compute stats: %s", exc)
        return out

    def titles(self) -> List[str]:
        """All queued titles (used to avoid regenerating the same movie)."""
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT title FROM content").fetchall()
            return [r["title"] for r in rows]
        except sqlite3.Error as exc:
            logger.error("Failed to list titles: %s", exc)
            return []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_json_list(value) -> List[str]:
    """Parse a JSON list column, tolerating bad data."""
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# Module-level singleton.
publish_queue = PublishQueue()
