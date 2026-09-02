"""
meta.py
=======
Meta Graph API integration for Facebook Pages and Instagram Business accounts.

Responsibilities:
    * Fetch recent comments on Facebook Page posts and Instagram media.
    * Reply to new comments via the Graph API comments endpoint.
    * Respect rate limits and never reply twice (SQLite dedup).

Auth:
    * Requires a long-lived Page access token (META_ACCESS_TOKEN) with the
      `pages_manage_posts`, `pages_read_engagement`, `instagram_basic`,
      `instagram_manage_comments` permissions.
"""

import logging
from typing import List

import requests

from config import settings
from database import db
from ai_handler import ai_handler

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.facebook.com"


class MetaHandler:
    """Handles Facebook Page and Instagram Business comment engagement."""

    name = "meta"

    def __init__(self) -> None:
        self.access_token = settings.meta_access_token
        self.api_version = settings.meta_api_version
        self.facebook_page_ids = settings.facebook_page_ids
        self.instagram_business_ids = settings.instagram_business_ids
        self.max_items = settings.max_comments_per_cycle

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _graph(self, path: str, params: dict) -> dict:
        """Perform a GET against the Graph API."""
        params = dict(params)
        params["access_token"] = self.access_token
        url = f"{GRAPH_BASE}/{self.api_version}/{path}"
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data: dict) -> dict:
        """Perform a POST against the Graph API."""
        data = dict(data)
        data["access_token"] = self.access_token
        url = f"{GRAPH_BASE}/{self.api_version}/{path}"
        resp = requests.post(url, data=data, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    # Fetching
    # ------------------------------------------------------------------ #
    def fetch_new_items(self) -> List[dict]:
        """Return new, unreplied comments from Facebook and Instagram."""
        if not self.access_token:
            logger.warning("Meta access token missing; skipping fetch.")
            return []

        items: List[dict] = []

        # Facebook Pages
        for page_id in self.facebook_page_ids:
            items.extend(self._fetch_facebook_page_comments(page_id))
            if len(items) >= self.max_items:
                return items

        # Instagram Business
        for ig_id in self.instagram_business_ids:
            items.extend(self._fetch_instagram_comments(ig_id))
            if len(items) >= self.max_items:
                return items

        return items

    def _fetch_facebook_page_comments(self, page_id: str) -> List[dict]:
        """Fetch comments on a Facebook Page's recent posts."""
        items: List[dict] = []
        try:
            posts = self._graph(
                f"{page_id}/posts",
                {"fields": "id,message", "limit": 10},
            ).get("data", [])
        except requests.exceptions.RequestException as exc:
            logger.error("Meta FB posts fetch failed for %s: %s", page_id, exc)
            return items

        for post in posts:
            post_id = post.get("id")
            if not post_id:
                continue
            try:
                comments = self._graph(
                    f"{post_id}/comments",
                    {"fields": "id,message,from,created_time", "limit": 25},
                ).get("data", [])
            except requests.exceptions.RequestException as exc:
                logger.error("Meta FB comments fetch failed for %s: %s", post_id, exc)
                continue

            for c in comments:
                comment_id = c.get("id")
                if not comment_id or db.is_replied(self.name, comment_id):
                    continue
                author = (c.get("from") or {}).get("name", "")
                items.append(
                    {
                        "platform": self.name,
                        "sub_platform": "facebook",
                        "item_id": comment_id,
                        "parent_id": post_id,
                        "text": c.get("message", ""),
                        "author": author,
                    }
                )
                if len(items) >= self.max_items:
                    return items
        return items

    def _fetch_instagram_comments(self, ig_id: str) -> List[dict]:
        """Fetch comments on an Instagram Business account's recent media."""
        items: List[dict] = []
        try:
            media = self._graph(
                f"{ig_id}/media",
                {"fields": "id,caption", "limit": 10},
            ).get("data", [])
        except requests.exceptions.RequestException as exc:
            logger.error("Meta IG media fetch failed for %s: %s", ig_id, exc)
            return items

        for m in media:
            media_id = m.get("id")
            if not media_id:
                continue
            try:
                comments = self._graph(
                    f"{media_id}/comments",
                    {"fields": "id,text,username,timestamp", "limit": 25},
                ).get("data", [])
            except requests.exceptions.RequestException as exc:
                logger.error("Meta IG comments fetch failed for %s: %s", media_id, exc)
                continue

            for c in comments:
                comment_id = c.get("id")
                if not comment_id or db.is_replied(self.name, comment_id):
                    continue
                items.append(
                    {
                        "platform": self.name,
                        "sub_platform": "instagram",
                        "item_id": comment_id,
                        "parent_id": media_id,
                        "text": c.get("text", ""),
                        "author": c.get("username", ""),
                    }
                )
                if len(items) >= self.max_items:
                    return items
        return items

    # ------------------------------------------------------------------ #
    # Posting
    # ------------------------------------------------------------------ #
    def post_reply(self, item: dict, reply: str) -> bool:
        """
        Post a reply to a Facebook or Instagram comment.
        Returns True on success.
        """
        comment_id = item["item_id"]
        try:
            self._post(f"{comment_id}/comments", {"message": reply})
            return True
        except requests.exceptions.HTTPError as exc:
            logger.error(
                "Meta reply failed for %s: %s", comment_id, exc
            )
            return False
        except requests.exceptions.RequestException as exc:
            logger.error("Meta reply network error: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Full process for one comment
    # ------------------------------------------------------------------ #
    def process_comment(self, item: dict) -> bool:
        """Generate and post a reply for a single comment item."""
        comment_text = item.get("text", "")
        if not comment_text:
            return False

        platform_label = (
            "Instagram" if item.get("sub_platform") == "instagram" else "Facebook"
        )
        reply = ai_handler.generate_reply(comment_text, platform_label)
        if not reply:
            logger.warning("No AI reply generated for Meta comment %s", item["item_id"])
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
            logger.info("Replied on %s to comment %s", platform_label, item["item_id"])
        return ok
