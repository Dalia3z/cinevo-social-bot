"""
discover_targets.py
===================
Discovery script for the Cinevo AI Social Media Engagement Bot.

Purpose:
    Automatically discover and collect movie/TV-related target IDs for the
    automation system:

    * YouTube  : prominent channels & videos specialised in cinema reviews,
                 recaps and summaries (targeting up to 10,000 items via
                 dynamic, paginated queries).
    * Meta     : active cinema / entertainment Facebook pages & communities
                 (and Instagram business accounts where available).

Storage:
    Because 10,000 IDs cannot live on a single `.env` line, discovered targets
    are written to a structured, partitioned `targets.json` file. This script
    also updates the relevant `.env` variables to point at that file.

Usage:
    python discover_targets.py --platform youtube --max 10000
    python discover_targets.py --platform meta
    python discover_targets.py --all --max 10000
    python discover_targets.py --dry-run          # fetch but do not save

Notes:
    * YouTube discovery needs YOUTUBE_API_KEY.
    * Meta discovery needs META_ACCESS_TOKEN (Graph API search). If missing,
      the script falls back to a curated seed list so the pipeline still runs.
    * Respects YouTube quota: uses a small delay between pages and a
      configurable max-results per request.
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from typing import Dict, List

import requests

from config import settings
import targets_loader

logger = logging.getLogger("discover")

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


# --------------------------------------------------------------------------- #
# YouTube discovery
# --------------------------------------------------------------------------- #
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Dynamic queries covering cinema reviews, recaps and summaries.
YOUTUBE_QUERIES = [
    "movie review",
    "film review",
    "movie recap",
    "film recap",
    "movie explained",
    "ending explained",
    "best movies",
    "top 10 movies",
    "movie trailer reaction",
    "film analysis",
    "cinema",
    "tv series review",
    "series recap",
    "netflix series",
    "movie summary",
    "blockbuster movies",
    "hollywood movies",
    "arabic movie review",
    "مراجعة فيلم",
    "ملخص فيلم",
    "شرح فيلم",
    "أفلام",
    "مسلسلات",
    "مراجعة مسلسل",
]

# Curated seed channels (fallback / bootstrap when API key is absent).
# These are REAL, well-known cinema/entertainment YouTube channel IDs so the
# bot has valid targets even before running a full discovery pass.
YOUTUBE_SEED_CHANNELS = [
    # Rotten Tomatoes (movie reviews)
    {"id": "UCi8e0iOVk1fEOogdfu4YgfA", "title": "Rotten Tomatoes"},
    # IGN (movies, TV, entertainment)
    {"id": "UCKy1dAqELo0zrOtPkf0eTMw", "title": "IGN"},
    # Screen Rant (movie/TV news & reviews)
    {"id": "UC2iUwfYi_1FCGGqhOUNx-iA", "title": "Screen Rant"},
    # Looper (movie/TV explainers)
    {"id": "UCaWd5_7JhbQBe4dknZhsHJg", "title": "Looper"},
    # CinemaSins (movie commentary)
    {"id": "UCYUQQgogVeQY8cMQamhHJcg", "title": "CinemaSins"},
]


def _yt_search(api_key: str, query: str, page_token: str = "", max_results: int = 50) -> Dict:
    """Perform a single YouTube search request."""
    params = {
        "part": "snippet",
        "q": query,
        "type": "video,channel",
        "maxResults": max_results,
        "relevanceLanguage": "en",
        "key": api_key,
    }
    if page_token:
        params["pageToken"] = page_token
    resp = requests.get(f"{YOUTUBE_API_BASE}/search", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def discover_youtube(api_key: str, target_count: int) -> Dict:
    """
    Discover YouTube channels and videos across dynamic queries.
    Returns {"channels": [...], "videos": [...]}.
    """
    channels: Dict[str, Dict] = {}
    videos: Dict[str, Dict] = {}
    total = 0

    if not api_key:
        logger.warning("No YOUTUBE_API_KEY; using curated seed channels only.")
        for c in YOUTUBE_SEED_CHANNELS:
            channels[c["id"]] = c
        return {"channels": list(channels.values()), "videos": []}

    # Rotate through queries to build a broad, diverse list.
    query_index = 0
    while total < target_count:
        query = YOUTUBE_QUERIES[query_index % len(YOUTUBE_QUERIES)]
        query_index += 1
        page_token = ""
        pages_without_new = 0

        while total < target_count and pages_without_new < 3:
            try:
                data = _yt_search(api_key, query, page_token)
            except requests.exceptions.HTTPError as exc:
                logger.error("YouTube search error for '%s': %s", query, exc)
                break
            except requests.exceptions.RequestException as exc:
                logger.error("YouTube network error: %s", exc)
                time.sleep(2)
                break

            items = data.get("items", [])
            added_this_page = 0
            for item in items:
                kind = item.get("id", {}).get("kind", "")
                snippet = item.get("snippet", {})
                title = snippet.get("title", "")
                if kind == "youtube#channel":
                    cid = item.get("id", {}).get("channelId")
                    if cid and cid not in channels:
                        channels[cid] = {"id": cid, "title": title, "kind": "channel"}
                        added_this_page += 1
                elif kind == "youtube#video":
                    vid = item.get("id", {}).get("videoId")
                    if vid and vid not in videos:
                        videos[vid] = {"id": vid, "title": title, "kind": "video"}
                        added_this_page += 1

            total = len(channels) + len(videos)
            logger.info(
                "Query '%s': +%d new (channels=%d videos=%d total=%d)",
                query, added_this_page, len(channels), len(videos), total,
            )

            if added_this_page == 0:
                pages_without_new += 1
            else:
                pages_without_new = 0

            # Respect quota: small random delay between pages.
            time.sleep(random.uniform(0.3, 0.8))

            page_token = data.get("nextPageToken", "")
            if not page_token:
                break

        # Stop if we've exhausted all queries.
        if query_index >= len(YOUTUBE_QUERIES) * 2:
            logger.info("Reached query rotation limit.")
            break

    return {"channels": list(channels.values()), "videos": list(videos.values())}


# --------------------------------------------------------------------------- #
# Meta discovery
# --------------------------------------------------------------------------- #
META_GRAPH_BASE = "https://graph.facebook.com"

# Curated seed pages (fallback when no token / no search results).
META_SEED_PAGES = [
    {"id": "cinema", "title": "Cinema (seed)"},
    {"id": "movies", "title": "Movies (seed)"},
]

META_QUERIES = ["movies", "cinema", "film", "tv series", "entertainment"]


def _meta_search(token: str, api_version: str, query: str) -> List[Dict]:
    """Search Facebook pages via the Graph API."""
    url = f"{META_GRAPH_BASE}/{api_version}/pages"
    params = {"q": query, "fields": "id,name,link", "access_token": token}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("data", [])


def discover_meta(token: str, api_version: str) -> Dict:
    """
    Discover Facebook pages/communities related to cinema & entertainment.
    Returns {"facebook_pages": [...], "instagram_business": [...]}.
    """
    pages: Dict[str, Dict] = {}

    if not token:
        logger.warning("No META_ACCESS_TOKEN; using curated seed pages only.")
        for p in META_SEED_PAGES:
            pages[p["id"]] = p
        return {"facebook_pages": list(pages.values()), "instagram_business": []}

    for query in META_QUERIES:
        try:
            results = _meta_search(token, api_version, query)
        except requests.exceptions.HTTPError as exc:
            logger.error("Meta search error for '%s': %s", query, exc)
            continue
        except requests.exceptions.RequestException as exc:
            logger.error("Meta network error: %s", exc)
            continue

        for r in results:
            pid = r.get("id")
            if pid and pid not in pages:
                pages[pid] = {
                    "id": pid,
                    "title": r.get("name", ""),
                    "link": r.get("link", ""),
                    "kind": "facebook_page",
                }
        logger.info("Meta query '%s': %d pages so far", query, len(pages))
        time.sleep(random.uniform(0.5, 1.0))

    return {"facebook_pages": list(pages.values()), "instagram_business": []}


# --------------------------------------------------------------------------- #
# .env updater
# --------------------------------------------------------------------------- #
def _update_env_file(env_path: str, updates: Dict[str, str]) -> None:
    """
    Update (or add) key=value pairs in a .env file while preserving comments
    and other lines. Used to point YOUTUBE_VIDEO_IDS / FACEBOOK_PAGE_IDS at
    the targets file (or to clear them so the JSON file is authoritative).
    """
    if not os.path.exists(env_path):
        logger.warning(".env not found at %s; skipping update.", env_path)
        return

    with open(env_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    keys = set(updates.keys())
    out = []
    for line in lines:
        stripped = line.strip()
        matched = False
        for key in keys:
            if stripped.startswith(key + "=") or stripped == key:
                out.append(f"{key}={updates[key]}\n")
                keys.discard(key)
                matched = True
                break
        if not matched:
            out.append(line)

    # Append any keys that were not present.
    for key, value in updates.items():
        if key in keys:
            out.append(f"{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.writelines(out)
    logger.info("Updated .env (%s) with %d variable(s).", env_path, len(updates))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover movie/TV target IDs for the Cinevo bot."
    )
    parser.add_argument(
        "--platform",
        choices=["youtube", "meta", "all"],
        default="all",
        help="Which platform(s) to discover.",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=10000,
        help="Maximum number of YouTube targets to collect (default 10000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch but do NOT save to targets.json or update .env.",
    )
    parser.add_argument(
        "--env",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        help="Path to the .env file to update.",
    )
    args = parser.parse_args()

    _setup_logging()

    # Load existing targets so we can merge (keep previously discovered IDs).
    data = targets_loader.load_targets()
    if not data.get("meta", {}).get("generated_at"):
        data = targets_loader._empty_structure()

    if args.platform in ("youtube", "all"):
        logger.info("=== Discovering YouTube targets (max %d) ===", args.max)
        yt = discover_youtube(settings.youtube_api_key, args.max)
        data["youtube"]["channels"] = yt["channels"]
        data["youtube"]["videos"] = yt["videos"]
        logger.info(
            "YouTube done: %d channels, %d videos",
            len(yt["channels"]), len(yt["videos"]),
        )

    if args.platform in ("meta", "all"):
        logger.info("=== Discovering Meta targets ===")
        meta = discover_meta(settings.meta_access_token, settings.meta_api_version)
        data["facebook"]["facebook_pages"] = meta["facebook_pages"]
        data["facebook"]["instagram_business"] = meta["instagram_business"]
        logger.info(
            "Meta done: %d pages, %d instagram accounts",
            len(meta["facebook_pages"]), len(meta["instagram_business"]),
        )

    data["meta"]["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data["meta"]["source"] = "discover_targets.py"

    total = targets_loader._count_targets(data)
    logger.info("Total targets collected: %d", total)

    if args.dry_run:
        logger.info("DRY-RUN: not saving. Would write %d targets.", total)
        return

    if not targets_loader.save_targets(data):
        logger.error("Failed to save targets.")
        sys.exit(1)

    # Update .env so the bot reads from the targets file.
    _update_env_file(
        args.env,
        {
            "TARGETS_FILE": "targets.json",
            # Clear inline lists so the JSON file is the single source of truth.
            "YOUTUBE_VIDEO_IDS": "",
            "FACEBOOK_PAGE_IDS": "",
            "INSTAGRAM_BUSINESS_IDS": "",
        },
    )
    logger.info("Discovery complete. Targets saved to targets.json.")


if __name__ == "__main__":
    main()
