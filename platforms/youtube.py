"""
youtube.py
==========
YouTube Shorts integration using the YouTube Data API v3.

Responsibilities:
    * Fetch recent top-level comments on the configured Shorts video IDs.
    * Reply to new comments via the `comments.insert` endpoint.
    * Respect quota/rate limits and never reply twice (SQLite dedup).

Auth note:
    * Reading comments requires only an API key (YOUTUBE_API_KEY).
    * *Posting* a reply (comments.insert) requires OAuth 2.0 with the
      `youtube.force-ssl` scope. Provide the token via YOUTUBE_OAUTH_TOKEN
      (a client access token) or extend this module to use a credentials file.
"""

import logging
import time
from typing import List, Optional

import requests

from config import settings
from database import db
from ai_handler import ai_handler

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeHandler:
    """Handles fetching and replying to YouTube Shorts comments."""

    name = "youtube"

    def __init__(self) -> None:
        self.api_key = settings.youtube_api_key
        self.video_ids = settings.youtube_video_ids
        # OAuth token used only for posting replies (comments.insert).
        self.oauth_token = settings.youtube_oauth_token
        self.max_items = settings.max_comments_per_cycle

    # ------------------------------------------------------------------ #
    # Fetching
    # ------------------------------------------------------------------ #
    def fetch_new_items(self) -> List[dict]:
        """
        Return a list of new, unreplied top-level comments across all
        configured Shorts videos.

        Each item dict:
            {
                "platform": "youtube",
                "item_id": <comment id>,
                "parent_id": <video id>,
                "text": <comment text>,
                "author": <author display name>,
            }
        """
        if not self.api_key:
            logger.warning("YouTube API key missing; skipping fetch.")
            return []

        items: List[dict] = []
        for video_id in self.video_ids:
            try:
                comments = self._fetch_comments_for_video(video_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("YouTube fetch failed for %s: %s", video_id, exc)
                continue

            for c in comments:
                comment_id = c.get("id")
                if not comment_id:
                    continue
                if db.is_replied(self.name, comment_id):
                    continue
                snippet = c.get("snippet", {})
                items.append(
                    {
                        "platform": self.name,
                        "item_id": comment_id,
                        "parent_id": video_id,
                        "text": snippet.get("textDisplay", ""),
                        "author": snippet.get("authorDisplayName", ""),
                    }
                )
                if len(items) >= self.max_items:
                    return items
        return items

    def _fetch_comments_for_video(self, video_id: str) -> List[dict]:
        """Fetch top-level comments for a single video (paginated, capped)."""
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": min(50, self.max_items),
            "textFormat": "plainText",
            "order": "time",  # newest first
            "key": self.api_key,
        }
        headers = {}
        if self.oauth_token:
            headers["Authorization"] = f"Bearer {self.oauth_token}"

        resp = requests.get(
            f"{API_BASE}/commentThreads", params=params, headers=headers, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    # ------------------------------------------------------------------ #
    # Posting
    # ------------------------------------------------------------------ #
    def post_reply(self, item: dict, reply: str) -> bool:
        """
        Post a reply to a comment via comments.insert.
        Returns True on success.
        """
        if not self.oauth_token:
            logger.error(
                "Cannot post YouTube reply: YOUTUBE_OAUTH_TOKEN not configured."
            )
            return False

        body = {
            "snippet": {
                "parentId": item["item_id"],
                "textOriginal": reply,
            }
        }
        headers = {
            "Authorization": f"Bearer {self.oauth_token}",
            "Content-Type": "application/json",
        }
        params = {"part": "snippet"}

        try:
            resp = requests.post(
                f"{API_BASE}/comments",
                json=body,
                params=params,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            return True
        except requests.exceptions.HTTPError as exc:
            logger.error(
                "YouTube reply failed for %s: %s | %s",
                item["item_id"],
                exc,
                resp.text[:500],
            )
            return False
        except requests.exceptions.RequestException as exc:
            logger.error("YouTube reply network error: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Convenience: full process for one comment (used by worker)
    # ------------------------------------------------------------------ #
    def process_comment(self, item: dict) -> bool:
        """
        Generate and post a reply for a single comment item.
        Returns True if a reply was successfully posted.
        """
        comment_text = item.get("text", "")
        if not comment_text:
            return False

        reply = ai_handler.generate_reply(comment_text, "YouTube Shorts")
        if not reply:
            logger.warning("No AI reply generated for YouTube comment %s", item["item_id"])
            return False

        ok = self.post_reply(item, reply)
        if ok:
            db.mark_replied(self.name, item["item_id"], item.get("parent_id"))
            db.log_reply(
                self.name,
                item["item_id"],
                item.get("parent_id"),
                comment_text,
                reply,
            )
            logger.info("Replied on YouTube to comment %s", item["item_id"])
        return ok
