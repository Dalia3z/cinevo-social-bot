"""
targets_loader.py
=================
Loads and manages the auto-discovered engagement targets stored in
`targets.json`.

Why a separate JSON file?
    Storing up to 10,000+ IDs directly inside a single `.env` line would make
    it huge, unreadable and error-prone. Instead the discovery script writes
    structured, partitioned data to `targets.json`, and this module exposes
    those lists to the rest of the bot.

File structure (see targets.json):
    {
      "meta":     { "generated_at": "...", "source": "...", "note": "..." },
      "youtube":  { "channels": [...], "videos": [...] },
      "facebook": { "facebook_pages": [...], "instagram_business": [...] }
    }

Each entry is a dict with at least an "id" field plus optional metadata:
    {"id": "UC...", "title": "Channel name", "kind": "channel"}

The bot merges these discovered targets with any IDs still provided directly
in `.env` (for backwards compatibility / manual overrides).
"""

import json
import logging
import os
from typing import Dict, List

from config import settings

logger = logging.getLogger(__name__)

# Default location of the targets file (next to this module).
DEFAULT_TARGETS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "targets.json"
)


def _empty_structure() -> Dict:
    """Return a fresh, empty targets structure."""
    return {
        "meta": {"generated_at": "", "source": "", "note": ""},
        "youtube": {"channels": [], "videos": []},
        "facebook": {"facebook_pages": [], "instagram_business": []},
    }


def load_targets(path: str = "") -> Dict:
    """
    Load the targets file. Returns an empty structure if the file is missing
    or malformed (never raises, so the bot can start without targets).
    """
    path = path or settings.targets_file
    if not os.path.exists(path):
        logger.info("Targets file not found at %s; using empty targets.", path)
        return _empty_structure()

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # Ensure all expected keys exist.
        structure = _empty_structure()
        structure["meta"].update(data.get("meta", {}))
        structure["youtube"].update(data.get("youtube", {}))
        structure["facebook"].update(data.get("facebook", {}))
        return structure
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load targets file %s: %s", path, exc)
        return _empty_structure()


def save_targets(data: Dict, path: str = "") -> bool:
    """Persist the targets structure to disk as pretty JSON."""
    path = path or settings.targets_file
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        logger.info("Saved %d targets to %s", _count_targets(data), path)
        return True
    except OSError as exc:
        logger.error("Failed to save targets to %s: %s", path, exc)
        return False


def _ids(entries: List) -> List[str]:
    """Extract the 'id' field from a list of entry dicts."""
    ids = []
    for e in entries:
        if isinstance(e, str):
            ids.append(e)
        elif isinstance(e, dict) and e.get("id"):
            ids.append(str(e["id"]))
    return ids


def _count_targets(data: Dict) -> int:
    """Count total target entries across all platforms."""
    total = 0
    for platform in ("youtube", "facebook"):
        for bucket in data.get(platform, {}).values():
            total += len(bucket)
    return total


class Targets:
    """Convenience accessor combining discovered targets + .env overrides."""

    def __init__(self, data: Dict) -> None:
        self.data = data

    # -- YouTube -------------------------------------------------------- #
    @property
    def youtube_channels(self) -> List[str]:
        """Discovered YouTube channel IDs."""
        return _ids(self.data.get("youtube", {}).get("channels", []))

    @property
    def youtube_videos(self) -> List[str]:
        """Discovered YouTube video IDs."""
        return _ids(self.data.get("youtube", {}).get("videos", []))

    # -- Meta ----------------------------------------------------------- #
    @property
    def facebook_pages(self) -> List[str]:
        """Discovered Facebook Page IDs."""
        return _ids(self.data.get("facebook", {}).get("facebook_pages", []))

    @property
    def instagram_business(self) -> List[str]:
        """Discovered Instagram Business Account IDs."""
        return _ids(self.data.get("facebook", {}).get("instagram_business", []))


def get_merged_youtube_video_ids() -> List[str]:
    """
    Return the full list of YouTube video IDs to monitor:
    discovered targets + any IDs from the .env YOUTUBE_VIDEO_IDS override.
    """
    discovered = Targets(load_targets()).youtube_videos
    merged = list(dict.fromkeys(discovered + settings.youtube_video_ids))
    return merged


def get_merged_youtube_channel_ids() -> List[str]:
    """
    Return the full list of YouTube channel IDs to monitor:
    discovered targets + any IDs from the .env YOUTUBE_CHANNEL_IDS override.
    """
    discovered = Targets(load_targets()).youtube_channels
    merged = list(dict.fromkeys(discovered + settings.youtube_channel_ids))
    return merged


def get_merged_facebook_page_ids() -> List[str]:
    """Return discovered Facebook Page IDs + .env overrides (deduplicated)."""
    discovered = Targets(load_targets()).facebook_pages
    merged = list(dict.fromkeys(discovered + settings.facebook_page_ids))
    return merged


def get_merged_instagram_business_ids() -> List[str]:
    """Return discovered Instagram IDs + .env overrides (deduplicated)."""
    discovered = Targets(load_targets()).instagram_business
    merged = list(dict.fromkeys(discovered + settings.instagram_business_ids))
    return merged
