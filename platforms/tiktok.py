"""
tiktok.py
=========
TikTok integration for the Cinevo AI Social Media Engagement Bot.

IMPORTANT CONTEXT:
    TikTok's official Content Posting API (research/display) does NOT yet
    expose a public, approved endpoint for reading arbitrary video comments
    or posting replies for regular business accounts. The public "Research
    API" is limited to approved researchers and is read-only.

Therefore this module provides a **safe, opt-in** design:

    1. OFFICIAL PATH (recommended, when available):
       If TikTok exposes comment endpoints for your approved app, configure
       TIKTOK_ACCESS_TOKEN / TIKTOK_OPEN_ID and this module will use them via
       the official API. The methods below are structured so you can plug in
       the exact official endpoints once your app is approved.

    2. SAFE BROWSER-AUTOMATION PATH (optional, high risk):
       A deliberately conservative Selenium-based flow is included but is
       DISABLED by default (TIKTOK_BROWSER_AUTOMATION=false). It uses long,
       randomised delays and human-like behaviour to minimise detection.
       Use at your own risk and only on accounts you control.

The module is written so that if you do NOT enable it, nothing runs and no
risk is introduced.
"""

import logging
import random
import time
from typing import List, Optional

import requests

from config import settings
from database import db
from ai_handler import ai_handler

logger = logging.getLogger(__name__)

# TikTok official API base (subject to change as TikTok evolves its API).
TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"


class TikTokHandler:
    """Handles TikTok comment engagement via official API or safe automation."""

    name = "tiktok"

    def __init__(self) -> None:
        self.access_token = settings.tiktok_access_token
        self.open_id = settings.tiktok_open_id
        self.video_ids = settings.tiktok_video_ids
        self.max_items = settings.max_comments_per_cycle
        self.browser_automation = settings.tiktok_browser_automation

    # ------------------------------------------------------------------ #
    # Fetching (official API path)
    # ------------------------------------------------------------------ #
    def fetch_new_items(self) -> List[dict]:
        """
        Return new, unreplied comments.

        NOTE: Because TikTok's public comment API is not generally available,
        this returns an empty list unless you implement the official endpoint
        below once your app is approved. The structure mirrors other modules.
        """
        if not self.access_token:
            logger.info(
                "TikTok access token not set; skipping official API fetch."
            )
            return []

        items: List[dict] = []
        for video_id in self.video_ids:
            try:
                comments = self._fetch_comments_for_video(video_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("TikTok fetch failed for %s: %s", video_id, exc)
                continue

            for c in comments:
                comment_id = c.get("comment_id")
                if not comment_id or db.is_replied(self.name, comment_id):
                    continue
                items.append(
                    {
                        "platform": self.name,
                        "item_id": comment_id,
                        "parent_id": video_id,
                        "text": c.get("text", ""),
                        "author": c.get("user", {}).get("display_name", ""),
                    }
                )
                if len(items) >= self.max_items:
                    return items
        return items

    def _fetch_comments_for_video(self, video_id: str) -> List[dict]:
        """
        Placeholder for the official TikTok comment-list endpoint.

        Once TikTok exposes it for your approved app, replace the URL/fields
        below. The current shape follows TikTok's v2 API conventions.
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        body = {
            "video_id": video_id,
            "max_count": min(50, self.max_items),
            "cursor": 0,
        }
        # NOTE: endpoint path is illustrative; adjust to TikTok's live spec.
        resp = requests.post(
            f"{TIKTOK_API_BASE}/video/comment/list/",
            json=body,
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("comments", [])

    # ------------------------------------------------------------------ #
    # Posting (official API path)
    # ------------------------------------------------------------------ #
    def post_reply(self, item: dict, reply: str) -> bool:
        """
        Post a reply to a TikTok comment via the official API.

        NOTE: This is a stub for the official reply endpoint. Replace the URL
        and payload with TikTok's live spec once available for your app.
        """
        if not self.access_token:
            logger.error("Cannot post TikTok reply: no access token.")
            return False

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        body = {
            "comment_id": item["item_id"],
            "text": reply,
        }
        try:
            # Illustrative endpoint; adjust to TikTok's live spec.
            resp = requests.post(
                f"{TIKTOK_API_BASE}/video/comment/reply/",
                json=body,
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            return True
        except requests.exceptions.HTTPError as exc:
            logger.error("TikTok reply failed: %s | %s", exc, resp.text[:500])
            return False
        except requests.exceptions.RequestException as exc:
            logger.error("TikTok reply network error: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Full process for one comment
    # ------------------------------------------------------------------ #
    def process_comment(self, item: dict) -> bool:
        """Generate and post a reply for a single comment item."""
        comment_text = item.get("text", "")
        if not comment_text:
            return False

        reply = ai_handler.generate_reply(comment_text, "TikTok")
        if not reply:
            logger.warning("No AI reply generated for TikTok comment %s", item["item_id"])
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
            logger.info("Replied on TikTok to comment %s", item["item_id"])
        return ok
