"""
config.py
=========
Central configuration loader for the Cinevo AI Social Media Engagement Bot.

Reads all environment variables / secrets from a `.env` file (or the process
environment) and exposes them as typed, validated attributes. This keeps
credentials out of the source code and makes the bot easy to configure on a
fresh VPS.

Usage:
    from config import settings
    print(settings.deepseek_api_key)
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from dotenv import load_dotenv

    # Load variables from a .env file located next to this module if present.
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:  # python-dotenv not installed -> rely on real env vars
    pass


def _get_str(name: str, default: str = "") -> str:
    """
    Read an environment variable as a string, treating an EMPTY or
    whitespace-only value as "not set" and falling back to `default`.

    This matters on GitHub Actions: an undefined repository *variable*
    expands to an empty string (e.g. DEEPSEEK_BASE_URL=''), and
    `os.getenv(name, default)` would return '' instead of the default,
    producing broken URLs like '/chat/completions'.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _get_bool(name: str, default: bool = False) -> bool:
    """Parse an environment variable as a boolean."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    """Parse an environment variable as a float, falling back to default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    """Parse an environment variable as an int, falling back to default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    """Typed settings container populated from the environment."""

    # ------------------------------------------------------------------ #
    # DeepSeek AI
    # ------------------------------------------------------------------ #
    deepseek_api_key: str = field(
        default_factory=lambda: _get_str("DEEPSEEK_API_KEY", "")
    )
    deepseek_base_url: str = field(
        default_factory=lambda: _get_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    )
    deepseek_model: str = field(
        default_factory=lambda: _get_str("DEEPSEEK_MODEL", "deepseek-chat")
    )
    deepseek_max_tokens: int = field(
        default_factory=lambda: _get_int("DEEPSEEK_MAX_TOKENS", 120)
    )
    deepseek_temperature: float = field(
        default_factory=lambda: _get_float("DEEPSEEK_TEMPERATURE", 0.7)
    )

    # ------------------------------------------------------------------ #
    # Brand / website link injected naturally into replies
    # ------------------------------------------------------------------ #
    website_url: str = field(
        default_factory=lambda: _get_str("WEBSITE_URL", "https://cinevoapp.com")
    )
    brand_name: str = field(
        default_factory=lambda: _get_str("BRAND_NAME", "Cinevo")
    )
    # How often (0.0 - 1.0) the website link should appear in a reply.
    # 1.0 = ALWAYS include the link (default) so the bot actually drives
    # traffic. Set to e.g. 0.8 to skip it occasionally, or 0.0 to never
    # include it.
    website_link_probability: float = field(
        default_factory=lambda: _get_float("WEBSITE_LINK_PROBABILITY", 1.0)
    )

    # ------------------------------------------------------------------ #
    # YouTube Data API v3
    # ------------------------------------------------------------------ #
    youtube_api_key: str = field(
        default_factory=lambda: _get_str("YOUTUBE_API_KEY", "")
    )
    # OAuth 2.0 access token required to POST replies (comments.insert).
    # NOTE: a raw access token expires after ~1 hour. For unattended runs
    # (GitHub Actions cron) prefer the refresh-token trio below so the bot
    # can mint a fresh access token automatically on every cycle.
    youtube_oauth_token: str = field(
        default_factory=lambda: _get_str("YOUTUBE_OAUTH_TOKEN", "")
    )
    # OAuth 2.0 refresh token (long-lived). When set together with the client
    # id/secret, the bot exchanges it for a fresh access token automatically.
    youtube_oauth_refresh_token: str = field(
        default_factory=lambda: _get_str("YOUTUBE_OAUTH_REFRESH_TOKEN", "")
    )
    # OAuth 2.0 client credentials used for the refresh-token exchange.
    youtube_oauth_client_id: str = field(
        default_factory=lambda: _get_str("YOUTUBE_OAUTH_CLIENT_ID", "")
    )
    youtube_oauth_client_secret: str = field(
        default_factory=lambda: _get_str("YOUTUBE_OAUTH_CLIENT_SECRET", "")
    )
    # Comma separated list of video IDs (Shorts) to monitor.
    youtube_video_ids: List[str] = field(default_factory=list)
    # Comma separated list of channel IDs to monitor (their recent uploads).
    youtube_channel_ids: List[str] = field(default_factory=list)
    youtube_enabled: bool = field(
        default_factory=lambda: _get_bool("YOUTUBE_ENABLED", False)
    )

    # ------------------------------------------------------------------ #
    # Meta Graph API (Facebook Pages + Instagram Business)
    # ------------------------------------------------------------------ #
    meta_access_token: str = field(
        default_factory=lambda: _get_str("META_ACCESS_TOKEN", "")
    )
    # Comma separated list of Facebook Page IDs.
    facebook_page_ids: List[str] = field(default_factory=list)
    # Comma separated list of Instagram Business Account IDs.
    instagram_business_ids: List[str] = field(default_factory=list)
    meta_enabled: bool = field(
        default_factory=lambda: _get_bool("META_ENABLED", False)
    )
    meta_api_version: str = field(
        default_factory=lambda: _get_str("META_API_VERSION", "v19.0")
    )

    # ------------------------------------------------------------------ #
    # TikTok
    # ------------------------------------------------------------------ #
    tiktok_enabled: bool = field(
        default_factory=lambda: _get_bool("TIKTOK_ENABLED", False)
    )
    tiktok_access_token: str = field(
        default_factory=lambda: _get_str("TIKTOK_ACCESS_TOKEN", "")
    )
    tiktok_open_id: str = field(
        default_factory=lambda: _get_str("TIKTOK_OPEN_ID", "")
    )
    # Comma separated list of TikTok video IDs to monitor.
    tiktok_video_ids: List[str] = field(default_factory=list)
    # Opt-in browser automation (high risk, disabled by default).
    tiktok_browser_automation: bool = field(
        default_factory=lambda: _get_bool("TIKTOK_BROWSER_AUTOMATION", False)
    )

    # ------------------------------------------------------------------ #
    # Quora
    # ------------------------------------------------------------------ #
    quora_enabled: bool = field(
        default_factory=lambda: _get_bool("QUORA_ENABLED", False)
    )
    # Comma separated list of topic keywords to search for (e.g. movies, series).
    quora_topics: List[str] = field(default_factory=list)
    # Optional: browser automation is used for Quora (no official public API).
    quora_username: str = field(
        default_factory=lambda: _get_str("QUORA_USERNAME", "")
    )
    quora_password: str = field(
        default_factory=lambda: _get_str("QUORA_PASSWORD", "")
    )
    # Opt-in browser automation (high risk, disabled by default -> manual queue).
    quora_browser_automation: bool = field(
        default_factory=lambda: _get_bool("QUORA_BROWSER_AUTOMATION", False)
    )

    # ------------------------------------------------------------------ #
    # Behaviour / anti-ban tuning
    # ------------------------------------------------------------------ #
    # Random delay range (seconds) between consecutive replies.
    # Kept short enough that several replies fit inside the runtime budget
    # (MAX_RUNTIME_SECONDS) while still looking human / avoiding bans.
    min_delay_seconds: float = field(
        default_factory=lambda: _get_float("MIN_DELAY_SECONDS", 60)  # 1 min
    )
    max_delay_seconds: float = field(
        default_factory=lambda: _get_float("MAX_DELAY_SECONDS", 150)  # 2.5 min
    )
    # Warm-up: for the first N replies the bot posts GENUINE comments with NO
    # website link. YouTube holds replies from brand-new channels for review
    # when they immediately post promotional links, so we build trust first.
    # After N replies the link is included normally.
    warmup_replies: int = field(
        default_factory=lambda: _get_int("WARMUP_REPLIES", 20)
    )
    # How often (seconds) the main loop polls each platform.
    poll_interval_seconds: int = field(
        default_factory=lambda: _get_int("POLL_INTERVAL_SECONDS", 300)
    )
    # Hard runtime budget (seconds) for a single `--once` cycle. The bot stops
    # starting new replies once this budget is exhausted and exits CLEANLY,
    # instead of being killed by GitHub Actions' `timeout-minutes` (which shows
    # up as "Error: The operation was canceled." and loses the run summary).
    # Default 780s (13 min) leaves ~2 min of headroom under a 15-min job limit.
    max_runtime_seconds: int = field(
        default_factory=lambda: _get_int("MAX_RUNTIME_SECONDS", 780)
    )
    # Max comments to fetch per platform per cycle.
    max_comments_per_cycle: int = field(
        default_factory=lambda: _get_int("MAX_COMMENTS_PER_CYCLE", 20)
    )
    # Max comments to take from a SINGLE video per cycle. Keeps replies spread
    # across many videos instead of hammering one video (which looks spammy).
    max_comments_per_video: int = field(
        default_factory=lambda: _get_int("MAX_COMMENTS_PER_VIDEO", 2)
    )

    # ------------------------------------------------------------------ #
    # Short-form content system (TikTok / YouTube Shorts)
    # ------------------------------------------------------------------ #
    # NOTE: This subsystem is COMPLETELY SEPARATE from the comment-reply bot.
    # It only generates text (scripts/captions) via content_cli.py and never
    # posts automatically, so it cannot affect the engagement loop.
    # Language for generated hooks/scripts/captions (hashtags stay English).
    content_language: str = field(
        default_factory=lambda: _get_str("CONTENT_LANGUAGE", "English")
    )
    # How many short videos to plan per day.
    content_posts_per_day: int = field(
        default_factory=lambda: _get_int("CONTENT_POSTS_PER_DAY", 3)
    )
    # Max tokens for content generation (scripts are longer than replies).
    content_max_tokens: int = field(
        default_factory=lambda: _get_int("CONTENT_MAX_TOKENS", 1200)
    )
    # Temperature for content generation (higher = more creative).
    content_temperature: float = field(
        default_factory=lambda: _get_float("CONTENT_TEMPERATURE", 0.9)
    )

    # ------------------------------------------------------------------ #
    # Short-form VIDEO generation + auto-publish (TikTok / YouTube Shorts)
    # ------------------------------------------------------------------ #
    # MASTER KILL-SWITCH for the video/auto-publish subsystem ONLY.
    # Set CONTENT_ENABLED=false to instantly stop video generation and
    # auto-publishing WITHOUT touching the comment-reply bot.
    content_enabled: bool = field(
        default_factory=lambda: _get_bool("CONTENT_ENABLED", False)
    )
    # AUTO-PUBLISH switch. Even when CONTENT_ENABLED=true, publishing stays
    # OFF unless this is explicitly true. This is the second safety gate so a
    # misconfiguration can never post to the live channel by accident.
    content_auto_publish: bool = field(
        default_factory=lambda: _get_bool("CONTENT_AUTO_PUBLISH", False)
    )
    # Hard daily cap on how many videos may be PUBLISHED per day. Kept at 1 by
    # default to minimise risk to the shared channel.
    content_daily_publish_limit: int = field(
        default_factory=lambda: _get_int("CONTENT_DAILY_PUBLISH_LIMIT", 1)
    )
    # Where rendered MP4 files are written (git-ignored).
    content_video_dir: str = field(
        default_factory=lambda: _get_str("CONTENT_VIDEO_DIR", "content_videos")
    )
    # Optional background music file (must be royalty-free). Empty = no music.
    content_music_path: str = field(
        default_factory=lambda: _get_str("CONTENT_MUSIC_PATH", "")
    )
    # Voiceover: enable/disable + the edge-tts voice name.
    content_enable_voice: bool = field(
        default_factory=lambda: _get_bool("CONTENT_ENABLE_VOICE", True)
    )
    content_voice: str = field(
        default_factory=lambda: _get_str("CONTENT_VOICE", "en-US-AriaNeural")
    )
    # YouTube privacy for auto-published videos: public | unlisted | private.
    # Defaults to `unlisted` so you can review before making it public.
    content_privacy: str = field(
        default_factory=lambda: _get_str("CONTENT_PRIVACY", "unlisted")
    )
    # YouTube category id (24 = Entertainment).
    content_category_id: str = field(
        default_factory=lambda: _get_str("CONTENT_CATEGORY_ID", "24")
    )

    # ------------------------------------------------------------------ #
    # VPS trailer pipeline (real footage from official YouTube trailers)
    # ------------------------------------------------------------------ #
    # NOTE: this pipeline is meant to run on a VPS, NOT on GitHub Actions.
    # It downloads official trailers with yt-dlp, cuts short clips out of
    # them, applies transformations (Ken Burns / colour grade / mirror /
    # speed ramp) and composes a vertical Short. See README for the
    # copyright discussion -- this is NOT risk-free.
    #
    # MASTER KILL-SWITCH for the trailer pipeline ONLY.
    trailer_enabled: bool = field(
        default_factory=lambda: _get_bool("TRAILER_ENABLED", False)
    )
    # Where downloaded source trailers are cached (git-ignored).
    trailer_clips_dir: str = field(
        default_factory=lambda: _get_str("TRAILER_CLIPS_DIR", "trailer_clips")
    )
    # Where the cut/transformed clips are written (git-ignored).
    trailer_output_dir: str = field(
        default_factory=lambda: _get_str("TRAILER_OUTPUT_DIR", "trailer_output")
    )
    # Timeout (seconds) for a single yt-dlp download. Trailers are small but
    # a slow VPS link can take a while; 600s is generous.
    trailer_download_timeout: int = field(
        default_factory=lambda: _get_int("TRAILER_DOWNLOAD_TIMEOUT", 600)
    )
    # Length (seconds) of each cut clip. Shorts work best with 2-4s cuts.
    trailer_clip_seconds: float = field(
        default_factory=lambda: _get_float("TRAILER_CLIP_SECONDS", 3.5)
    )
    # How many clips to cut from a single trailer.
    trailer_clips_per_video: int = field(
        default_factory=lambda: _get_int("TRAILER_CLIPS_PER_VIDEO", 5)
    )
    # How many trailers to download per run.
    trailer_downloads_per_run: int = field(
        default_factory=lambda: _get_int("TRAILER_DOWNLOADS_PER_RUN", 2)
    )
    # Comma separated list of trailer URLs to use (optional). When empty the
    # runner falls back to searching YouTube for "<title> official trailer".
    trailer_source_urls: List[str] = field(default_factory=list)
    # Comma separated list of movie/TV titles to build videos around.
    trailer_titles: List[str] = field(default_factory=list)
    # Transformation toggles. All default ON: the more the footage is
    # transformed, the weaker a Content ID match tends to be.
    trailer_ken_burns: bool = field(
        default_factory=lambda: _get_bool("TRAILER_KEN_BURNS", True)
    )
    trailer_color_grade: bool = field(
        default_factory=lambda: _get_bool("TRAILER_COLOR_GRADE", True)
    )
    trailer_mirror: bool = field(
        default_factory=lambda: _get_bool("TRAILER_MIRROR", False)
    )
    trailer_speed_ramp: bool = field(
        default_factory=lambda: _get_bool("TRAILER_SPEED_RAMP", False)
    )
    # Optional royalty-free music bed for the composed video.
    trailer_music_path: str = field(
        default_factory=lambda: _get_str("TRAILER_MUSIC_PATH", "")
    )
    # Attribution: append the source trailer URL to the description. Keeps
    # the upload honest and is a small goodwill gesture with rights holders.
    trailer_attribution: bool = field(
        default_factory=lambda: _get_bool("TRAILER_ATTRIBUTION", True)
    )

    # Master kill-switch for the whole bot.
    bot_enabled: bool = field(
        default_factory=lambda: _get_bool("BOT_ENABLED", True)
    )
    # Optional list of platforms to run (youtube, meta, tiktok, quora).
    active_platforms: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Database
    # ------------------------------------------------------------------ #
    db_path: str = field(
        default_factory=lambda: os.getenv(
            "DB_PATH",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_state.db"),
        )
    )
    # Path to the auto-discovered targets file (see targets_loader.py).
    targets_file: str = field(
        default_factory=lambda: os.getenv(
            "TARGETS_FILE",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "targets.json"),
        )
    )
    # Optional: cap on how many discovered targets the bot actually monitors.
    # 0 (default) means "use all discovered targets".
    max_targets_per_platform: int = field(
        default_factory=lambda: _get_int("MAX_TARGETS_PER_PLATFORM", 0)
    )

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    log_level: str = field(
        default_factory=lambda: _get_str("LOG_LEVEL", "INFO")
    )
    log_file: str = field(
        default_factory=lambda: os.getenv(
            "LOG_FILE",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "bot.log"),
        )
    )

    def __post_init__(self) -> None:
        """Normalise list-typed fields after construction."""
        self.youtube_video_ids = self._split_list(
            os.getenv("YOUTUBE_VIDEO_IDS", "")
        )
        self.youtube_channel_ids = self._split_list(
            os.getenv("YOUTUBE_CHANNEL_IDS", "")
        )
        self.facebook_page_ids = self._split_list(
            os.getenv("FACEBOOK_PAGE_IDS", "")
        )
        self.instagram_business_ids = self._split_list(
            os.getenv("INSTAGRAM_BUSINESS_IDS", "")
        )
        self.tiktok_video_ids = self._split_list(os.getenv("TIKTOK_VIDEO_IDS", ""))
        self.quora_topics = self._split_list(os.getenv("QUORA_TOPICS", ""))
        self.active_platforms = self._split_list(os.getenv("ACTIVE_PLATFORMS", ""))
        self.trailer_source_urls = self._split_list(
            os.getenv("TRAILER_SOURCE_URLS", "")
        )
        self.trailer_titles = self._split_list(os.getenv("TRAILER_TITLES", ""))

    @staticmethod
    def _split_list(raw: Optional[str]) -> List[str]:
        """Split a comma separated env string into a clean list."""
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def validate(self) -> List[str]:
        """
        Return a list of configuration problems (empty list == all good).
        Does NOT raise, so main.py can decide how to handle missing keys.
        """
        problems: List[str] = []
        if not self.deepseek_api_key:
            problems.append("DEEPSEEK_API_KEY is missing.")
        if self.bot_enabled and not self.active_platforms:
            problems.append(
                "ACTIVE_PLATFORMS is empty. Set e.g. youtube,meta,tiktok,quora"
            )
        if "youtube" in self.active_platforms and not self.youtube_api_key:
            problems.append("YOUTUBE_API_KEY is missing but youtube is active.")
        if "meta" in self.active_platforms and not self.meta_access_token:
            problems.append("META_ACCESS_TOKEN is missing but meta is active.")
        if self.min_delay_seconds > self.max_delay_seconds:
            problems.append("MIN_DELAY_SECONDS must be <= MAX_DELAY_SECONDS.")
        return problems


# A single, importable instance used across the whole application.
settings = Settings()
