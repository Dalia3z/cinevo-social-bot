"""
youtube_uploader.py
===================
Uploads a rendered short video to YouTube (Shorts) via the YouTube Data API v3
`videos.insert` endpoint, using the SAME OAuth credentials the reply bot uses.

SAFETY GATES (all must pass before anything is uploaded)
--------------------------------------------------------
1. CONTENT_ENABLED=true          - master kill-switch for this subsystem.
2. CONTENT_AUTO_PUBLISH=true     - explicit opt-in to actually publish.
3. Daily cap not exceeded        - CONTENT_DAILY_PUBLISH_LIMIT (default 1).
4. OAuth credentials present     - refresh token + client id/secret.

If ANY gate fails, `publish()` returns None and logs the reason. Nothing is
ever uploaded by accident.

ISOLATION
---------
This module is COMPLETELY SEPARATE from the comment-reply bot. It never touches
platforms/youtube.py, ai_handler.generate_reply(), or the reply database. It
only READS the shared OAuth settings and calls `videos.insert`.

NOTE ON SHORTS
--------------
YouTube treats a video as a Short when it is vertical (9:16) and <= 60s.
We add "#Shorts" to the description as an extra signal.
"""

import json
import logging
import os
from datetime import date
from typing import Dict, Optional

import requests

from config import settings
from publish_queue import publish_queue

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


class YouTubeUploader:
    """Uploads short videos to YouTube with strict safety gates."""

    def __init__(self) -> None:
        self.refresh_token = settings.youtube_oauth_refresh_token
        self.client_id = settings.youtube_oauth_client_id
        self.client_secret = settings.youtube_oauth_client_secret
        self.privacy = settings.content_privacy
        self.category_id = settings.content_category_id

    # ------------------------------------------------------------------ #
    # Safety gates
    # ------------------------------------------------------------------ #
    def _gate_reason(self) -> Optional[str]:
        """Return a human-readable reason if publishing is blocked, else None."""
        if not settings.content_enabled:
            return "CONTENT_ENABLED is false (kill-switch engaged)."
        if not settings.content_auto_publish:
            return "CONTENT_AUTO_PUBLISH is false (publishing not opted in)."
        if not (self.refresh_token and self.client_id and self.client_secret):
            return (
                "YouTube OAuth credentials missing "
                "(need YOUTUBE_OAUTH_REFRESH_TOKEN + CLIENT_ID + CLIENT_SECRET)."
            )
        published_today = publish_queue.published_today()
        if published_today >= settings.content_daily_publish_limit:
            return (
                f"Daily publish limit reached "
                f"({published_today}/{settings.content_daily_publish_limit})."
            )
        return None

    def can_publish(self) -> bool:
        """True when every safety gate passes."""
        reason = self._gate_reason()
        if reason:
            logger.info("Auto-publish blocked: %s", reason)
            return False
        return True

    # ------------------------------------------------------------------ #
    # OAuth
    # ------------------------------------------------------------------ #
    def _access_token(self) -> Optional[str]:
        """Exchange the refresh token for a fresh access token."""
        try:
            resp = requests.post(
                TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.error("OAuth token request failed: %s", exc)
            return None

        if resp.status_code != 200:
            logger.error(
                "OAuth token refresh failed (%s): %s", resp.status_code, resp.text[:500]
            )
            return None

        token = resp.json().get("access_token")
        if token:
            logger.info("YouTube OAuth token refreshed successfully.")
        return token

    # ------------------------------------------------------------------ #
    # Metadata
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_metadata(package: Dict) -> Dict:
        """Build the videos.insert request body from a content package."""
        title = str(package.get("title") or "Movie Recommendation").strip()
        hook = str(package.get("hook") or "").strip()
        caption = str(package.get("caption") or "").strip()
        cta = str(package.get("cta") or "").strip()
        hashtags = package.get("hashtags") or []

        # YouTube titles are capped at 100 chars.
        yt_title = f"{hook} #Shorts" if hook else f"{title} #Shorts"
        yt_title = yt_title[:100]

        tags = [str(t).lstrip("#") for t in hashtags][:15]

        description_parts = [caption, cta, "", " ".join(str(h) for h in hashtags)]
        description = "\n".join(p for p in description_parts if p).strip()
        if "#Shorts" not in description:
            description = (description + "\n\n#Shorts").strip()

        return {
            "snippet": {
                "title": yt_title,
                "description": description[:5000],
                "tags": tags,
                "categoryId": settings.content_category_id,
            },
            "status": {
                "privacyStatus": settings.content_privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

    # ------------------------------------------------------------------ #
    # Upload
    # ------------------------------------------------------------------ #
    def publish(
        self,
        video_path: str,
        package: Dict,
        queue_id: Optional[int] = None,
    ) -> Optional[str]:
        """
        Upload `video_path` to YouTube. Returns the video URL on success.

        All safety gates are checked first; if any fails, returns None.
        """
        reason = self._gate_reason()
        if reason:
            logger.warning("Refusing to publish: %s", reason)
            return None

        if not os.path.isfile(video_path):
            logger.error("Video file not found: %s", video_path)
            return None

        token = self._access_token()
        if not token:
            return None

        metadata = self._build_metadata(package)
        size = os.path.getsize(video_path)
        logger.info(
            "Uploading '%s' (%.1f MB) as %s...",
            metadata["snippet"]["title"],
            size / (1024 * 1024),
            self.privacy,
        )

        # Resumable upload: start a session, then PUT the bytes.
        try:
            init = requests.post(
                UPLOAD_URL,
                params={
                    "uploadType": "resumable",
                    "part": "snippet,status",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(size),
                },
                data=json.dumps(metadata),
                timeout=60,
            )
        except requests.RequestException as exc:
            logger.error("Upload session init failed: %s", exc)
            return None

        if init.status_code not in (200, 201):
            logger.error(
                "Upload session init rejected (%s): %s",
                init.status_code,
                init.text[:500],
            )
            return None

        session_url = init.headers.get("Location")
        if not session_url:
            logger.error("No resumable session URL returned by YouTube.")
            return None

        try:
            with open(video_path, "rb") as fh:
                upload = requests.put(
                    session_url,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(size),
                    },
                    data=fh,
                    timeout=900,
                )
        except requests.RequestException as exc:
            logger.error("Video upload failed: %s", exc)
            return None

        if upload.status_code not in (200, 201):
            logger.error(
                "Video upload rejected (%s): %s", upload.status_code, upload.text[:500]
            )
            return None

        video_id = upload.json().get("id")
        if not video_id:
            logger.error("Upload succeeded but no video id returned.")
            return None

        url = f"https://www.youtube.com/shorts/{video_id}"
        logger.info("Published to YouTube: %s", url)

        if queue_id is not None:
            publish_queue.set_status(
                queue_id, "posted", platform="youtube", posted_url=url
            )
        return url


# Module singleton.
youtube_uploader = YouTubeUploader()
