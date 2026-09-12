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
import targets_loader

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeHandler:
    """Handles fetching and replying to YouTube Shorts comments."""

    name = "youtube"

    def __init__(self) -> None:
        self.api_key = settings.youtube_api_key
        # Merge discovered targets (targets.json) with any .env overrides.
        self.video_ids = targets_loader.get_merged_youtube_video_ids()
        # Discovered channels whose recent uploads we also monitor.
        self.channel_ids = targets_loader.get_merged_youtube_channel_ids()
        # OAuth token used only for posting replies (comments.insert).
        self.oauth_token = settings.youtube_oauth_token
        self.max_items = settings.max_comments_per_cycle
        # Optional cap on how many targets to actually monitor this cycle.
        self.max_targets = settings.max_targets_per_platform
        if self.max_targets:
            self.video_ids = self.video_ids[: self.max_targets]
            self.channel_ids = self.channel_ids[: self.max_targets]

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

        # Self-test the API key ONCE per cycle so the log clearly states
        # whether the key itself works, independent of the channel IDs.
        self._selftest_api_key()
        # Self-test the OAuth token so we know up-front whether replies can
        # actually be POSTED (reading only needs the API key).
        self._selftest_oauth_token()

        # Build the set of video IDs to monitor: direct video targets plus the
        # recent uploads of discovered channels.
        video_ids = list(self.video_ids)
        for channel_id in self.channel_ids:
            try:
                uploads = self._fetch_channel_uploads(channel_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("YouTube channel uploads failed for %s: %s", channel_id, exc)
                continue
            for vid in uploads:
                if vid not in video_ids:
                    video_ids.append(vid)

        items: List[dict] = []
        for video_id in video_ids:
            try:
                comments = self._fetch_comments_for_video(video_id)
            except Exception as exc:  # noqa: BLE001
                logger.error("YouTube fetch failed for %s: %s", video_id, exc)
                continue

            for c in comments:
                # A commentThread item nests the actual comment under
                # snippet.topLevelComment. The thread's own `id` is NOT the
                # comment id we must reply to, and the text lives inside
                # topLevelComment.snippet.textDisplay (NOT snippet.textDisplay).
                thread_snippet = c.get("snippet", {})
                top = thread_snippet.get("topLevelComment", {})
                top_snippet = top.get("snippet", {})

                # The real comment id (used by comments.insert parentId).
                comment_id = top.get("id") or c.get("id")
                if not comment_id:
                    continue
                if db.is_replied(self.name, comment_id):
                    continue

                text = top_snippet.get("textDisplay", "") or top_snippet.get(
                    "textOriginal", ""
                )
                if not text:
                    # Nothing to reply to; skip silently (avoids wasted calls).
                    continue

                items.append(
                    {
                        "platform": self.name,
                        "item_id": comment_id,
                        "parent_id": video_id,
                        "text": text,
                        "author": top_snippet.get("authorDisplayName", ""),
                    }
                )
                if len(items) >= self.max_items:
                    return items
        return items

    def _selftest_api_key(self) -> None:
        """
        Verify the YouTube API key works by calling a cheap, always-valid
        endpoint. This isolates "bad API key" from "bad channel ID" so the
        logs are unambiguous.

        We call `videos?part=id&chart=mostPopular` which requires only a valid
        key and the YouTube Data API v3 enabled. Any error body is logged.
        """
        params = {
            "part": "id",
            "chart": "mostPopular",
            "maxResults": 1,
            "key": self.api_key,
        }
        try:
            resp = requests.get(f"{API_BASE}/videos", params=params, timeout=30)
        except requests.exceptions.RequestException as exc:
            logger.error("YouTube API key self-test network error: %s", exc)
            return

        if resp.status_code == 200:
            logger.info(
                "YouTube API key self-test: OK (key is valid and the "
                "YouTube Data API v3 is enabled)."
            )
            return

        # Log the raw Google error so the exact cause is visible.
        logger.error(
            "YouTube API key self-test FAILED (HTTP %s). Google says: %s",
            resp.status_code,
            resp.text[:500],
        )
        logger.error(
            "Likely causes: (1) YOUTUBE_API_KEY is wrong/truncated, "
            "(2) the YouTube Data API v3 is NOT enabled for this key's "
            "Google Cloud project, or (3) the key has HTTP referrer/IP "
            "restrictions that block server-side calls."
        )

    def _selftest_oauth_token(self) -> None:
        """
        Verify the OAuth token can actually POST (comments.insert requires the
        `youtube.force-ssl` scope). We call a cheap authenticated endpoint
        (`channels?part=snippet&mine=true`) which fails with 401 if the token
        is invalid/expired. This makes the log unambiguous BEFORE we try to
        post 20 replies and fail 20 times.
        """
        if not self.oauth_token:
            logger.warning(
                "YOUTUBE_OAUTH_TOKEN is not set. The bot can READ comments and "
                "generate DeepSeek replies, but it CANNOT POST them. Add a "
                "valid OAuth 2.0 token (scope 'youtube.force-ssl') as the "
                "YOUTUBE_OAUTH_TOKEN secret to enable posting."
            )
            return

        headers = {"Authorization": f"Bearer {self.oauth_token}"}
        params = {"part": "snippet", "mine": "true"}
        try:
            resp = requests.get(
                f"{API_BASE}/channels",
                params=params,
                headers=headers,
                timeout=30,
            )
        except requests.exceptions.RequestException as exc:
            logger.error("YouTube OAuth token self-test network error: %s", exc)
            return

        if resp.status_code == 200:
            logger.info(
                "YouTube OAuth token self-test: OK (token is valid and can "
                "post replies)."
            )
            return

        logger.error(
            "YouTube OAuth token self-test FAILED (HTTP %s). Google says: %s",
            resp.status_code,
            resp.text[:500],
        )
        logger.error(
            "The YOUTUBE_OAUTH_TOKEN secret is INVALID or EXPIRED. Posting "
            "replies requires a valid OAuth 2.0 access token with the "
            "'youtube.force-ssl' scope. Generate a fresh token and update the "
            "secret. (Reading comments still works via the API key.)"
        )

    def _fetch_channel_uploads(self, channel_id: str) -> List[str]:
        """
        Fetch the most recent video IDs uploaded by a channel.
        Uses the channel's uploads playlist (contentDetails.relatedPlaylists.uploads).
        """
        # 1) Resolve the uploads playlist id for the channel.
        params = {
            "part": "contentDetails",
            "id": channel_id,
            "key": self.api_key,
        }
        resp = requests.get(f"{API_BASE}/channels", params=params, timeout=30)

        # Give a CLEAR, actionable error instead of a bare "400 Bad Request".
        # We also log the RAW response body from Google, because it contains
        # the exact reason (e.g. "API key not valid", "API not enabled",
        # "quotaExceeded"). This is the fastest way to diagnose the failure.
        if resp.status_code == 400:
            logger.error(
                "YouTube rejected channel id '%s' (400 Bad Request). "
                "Google says: %s",
                channel_id,
                resp.text[:500],
            )
            return []
        if resp.status_code == 403:
            logger.error(
                "YouTube API key rejected (403 Forbidden). Google says: %s",
                resp.text[:500],
            )
            return []
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            logger.warning(
                "YouTube returned no channel for id '%s' (it may have been "
                "deleted or the ID is wrong).",
                channel_id,
            )
            return []
        uploads_playlist = (
            items[0].get("contentDetails", {})
            .get("relatedPlaylists", {})
            .get("uploads")
        )
        if not uploads_playlist:
            return []

        # 2) Fetch recent videos from that uploads playlist.
        params = {
            "part": "contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": min(10, self.max_items),
            "key": self.api_key,
        }
        resp = requests.get(f"{API_BASE}/playlistItems", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return [
            item.get("contentDetails", {}).get("videoId")
            for item in data.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        ]

    def _fetch_comments_for_video(self, video_id: str) -> List[dict]:
        """
        Fetch top-level comments for a single video (paginated, capped).

        IMPORTANT: reading public comments requires ONLY the API key. We must
        NOT send an `Authorization: Bearer` header here, because:
          * If YOUTUBE_OAUTH_TOKEN is empty, `Bearer ` (empty) makes Google
            return `401 Unauthorized`.
          * If the token is expired/invalid, Google also returns `401`.
        The OAuth token is only needed for POSTING replies (comments.insert),
        which is handled separately in `post_reply`.
        """
        params = {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": min(50, self.max_items),
            "textFormat": "plainText",
            "order": "time",  # newest first
            "key": self.api_key,
        }

        resp = requests.get(
            f"{API_BASE}/commentThreads", params=params, timeout=30
        )

        # Give a CLEAR, actionable error instead of a bare "401 Unauthorized".
        if resp.status_code == 401:
            logger.error(
                "YouTube commentThreads returned 401 Unauthorized for video "
                "'%s'. Google says: %s",
                video_id,
                resp.text[:500],
            )
            return []
        if resp.status_code == 403:
            logger.error(
                "YouTube commentThreads returned 403 Forbidden for video "
                "'%s' (comments may be disabled or quota exceeded). "
                "Google says: %s",
                video_id,
                resp.text[:500],
            )
            return []
        if resp.status_code == 404:
            # Video deleted/private, or comments disabled. Not fatal.
            logger.warning(
                "YouTube commentThreads 404 for video '%s' (deleted/private "
                "or comments disabled).",
                video_id,
            )
            return []

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
            if resp.status_code == 401:
                logger.error(
                    "YouTube reply failed for %s: 401 Unauthorized. "
                    "YOUTUBE_OAUTH_TOKEN is INVALID or EXPIRED. "
                    "Posting replies requires a valid OAuth 2.0 access token "
                    "with the 'youtube.force-ssl' scope. Generate a fresh "
                    "token and update the YOUTUBE_OAUTH_TOKEN secret. "
                    "Google says: %s",
                    item["item_id"],
                    resp.text[:500],
                )
            elif resp.status_code == 403:
                logger.error(
                    "YouTube reply failed for %s: 403 Forbidden. The OAuth "
                    "token lacks the 'youtube.force-ssl' scope, or the quota "
                    "is exceeded. Google says: %s",
                    item["item_id"],
                    resp.text[:500],
                )
            else:
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
            logger.warning(
                "Skipping YouTube comment %s: empty text (nothing to reply to).",
                item.get("item_id"),
            )
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
