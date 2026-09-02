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


def _get_bool(name: str, default: bool = False) -> bool:
    """Parse an environment variable as a boolean."""
    raw = os.getenv(name)
    if raw is None:
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
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", "")
    )
    deepseek_base_url: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    )
    deepseek_model: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
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
        default_factory=lambda: os.getenv("WEBSITE_URL", "https://cinevoapp.com")
    )
    brand_name: str = field(
        default_factory=lambda: os.getenv("BRAND_NAME", "Cinevo")
    )

    # ------------------------------------------------------------------ #
    # YouTube Data API v3
    # ------------------------------------------------------------------ #
    youtube_api_key: str = field(
        default_factory=lambda: os.getenv("YOUTUBE_API_KEY", "")
    )
    # OAuth 2.0 access token required to POST replies (comments.insert).
    youtube_oauth_token: str = field(
        default_factory=lambda: os.getenv("YOUTUBE_OAUTH_TOKEN", "")
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
        default_factory=lambda: os.getenv("META_ACCESS_TOKEN", "")
    )
    # Comma separated list of Facebook Page IDs.
    facebook_page_ids: List[str] = field(default_factory=list)
    # Comma separated list of Instagram Business Account IDs.
    instagram_business_ids: List[str] = field(default_factory=list)
    meta_enabled: bool = field(
        default_factory=lambda: _get_bool("META_ENABLED", False)
    )
    meta_api_version: str = field(
        default_factory=lambda: os.getenv("META_API_VERSION", "v19.0")
    )

    # ------------------------------------------------------------------ #
    # TikTok
    # ------------------------------------------------------------------ #
    tiktok_enabled: bool = field(
        default_factory=lambda: _get_bool("TIKTOK_ENABLED", False)
    )
    tiktok_access_token: str = field(
        default_factory=lambda: os.getenv("TIKTOK_ACCESS_TOKEN", "")
    )
    tiktok_open_id: str = field(
        default_factory=lambda: os.getenv("TIKTOK_OPEN_ID", "")
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
        default_factory=lambda: os.getenv("QUORA_USERNAME", "")
    )
    quora_password: str = field(
        default_factory=lambda: os.getenv("QUORA_PASSWORD", "")
    )
    # Opt-in browser automation (high risk, disabled by default -> manual queue).
    quora_browser_automation: bool = field(
        default_factory=lambda: _get_bool("QUORA_BROWSER_AUTOMATION", False)
    )

    # ------------------------------------------------------------------ #
    # Behaviour / anti-ban tuning
    # ------------------------------------------------------------------ #
    # Random delay range (seconds) between consecutive replies.
    min_delay_seconds: float = field(
        default_factory=lambda: _get_float("MIN_DELAY_SECONDS", 180)  # 3 min
    )
    max_delay_seconds: float = field(
        default_factory=lambda: _get_float("MAX_DELAY_SECONDS", 420)  # 7 min
    )
    # How often (seconds) the main loop polls each platform.
    poll_interval_seconds: int = field(
        default_factory=lambda: _get_int("POLL_INTERVAL_SECONDS", 300)
    )
    # Max comments to fetch per platform per cycle.
    max_comments_per_cycle: int = field(
        default_factory=lambda: _get_int("MAX_COMMENTS_PER_CYCLE", 20)
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
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
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
