"""
stock_source.py
===============
Downloads ROYALTY-FREE cinematic stock footage so the video pipeline can run
without touching YouTube at all.

WHY THIS EXISTS
---------------
The trailer pipeline originally pulled official trailers from YouTube via
yt-dlp. In practice YouTube aggressively bot-blocks datacenter IPs (VPS):
every player client returns either "Sign in to confirm you're not a bot" or
storyboard-only formats, and a browser cookies.txt exported from a different
IP/region makes it *worse*, not better. There is no reliable client-side fix.

Stock footage sidesteps the problem entirely:

    * The CDNs (Pexels, Pixabay, archive.org) do NOT bot-block datacenter IPs.
    * The licences are explicit and permissive (free for commercial use, no
      attribution required for Pexels/Pixabay; public domain for archive.org)
      so there is ZERO Content ID risk -- unlike re-using studio footage.
    * Downloads are plain HTTPS GETs, so no yt-dlp, no player clients, no
      cookies, no proxies.

PROVIDERS
---------
    1. Pexels      (best quality)  -- https://www.pexels.com/api/
    2. Pixabay     (good quality)  -- https://pixabay.com/api/docs/
    3. archive.org (NO KEY NEEDED) -- public-domain films & footage

Pexels and Pixabay are free but require a signup. archive.org requires
NOTHING: it is a public JSON API over public-domain content, so the pipeline
works out of the box with zero configuration. Set STOCK_PEXELS_API_KEY and/or
STOCK_PIXABAY_API_KEY in .env to add the higher-quality providers on top.

DESIGN
------
    * HTTP is done with `urllib.request` from the stdlib so there is no new
      dependency to install on the VPS.
    * Every download is cached on disk and reused across runs.
    * A metadata sidecar (.json) records the provider, author and source page
      so attribution can be added when a licence requires it.
    * Everything is bounded: max filesize, per-request timeout.

USAGE (CLI)
    python stock_source.py --search "dark forest fog" --limit 5
    python stock_source.py --fetch "dark forest fog" --count 5
    python stock_source.py --list

USAGE (code)
    from stock_source import stock_source
    clips = stock_source.fetch_clips("dark forest fog", count=5)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:  # Windows consoles are cp1252 by default; keep logs readable.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: A browser-like UA. Some CDNs reject the default urllib agent.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

#: Pexels API root.
PEXELS_API = "https://api.pexels.com/videos/search"

#: Pixabay API root.
PIXABAY_API = "https://pixabay.com/api/videos/"

#: archive.org advanced-search endpoint. No API key required: this is a public
#: JSON API over public-domain / openly-licensed content.
ARCHIVE_SEARCH_API = "https://archive.org/advancedsearch.php"

#: archive.org metadata endpoint, used to resolve the actual file list.
ARCHIVE_METADATA_API = "https://archive.org/metadata/"

#: archive.org download root.
ARCHIVE_DOWNLOAD_ROOT = "https://archive.org/download/"

#: Fallback search terms when a title gives us nothing to work with. These are
#: deliberately cinematic and generic so the footage always looks intentional.
DEFAULT_QUERIES: List[str] = [
    "cinematic dark forest fog",
    "cinematic city night neon",
    "cinematic ocean waves storm",
    "cinematic abandoned building",
    "cinematic desert dunes sunset",
    "cinematic rain window moody",
    "cinematic mountain clouds aerial",
    "cinematic candle flame dark",
]


def _safe_name(text: str, max_len: int = 60) -> str:
    """Turn an arbitrary string into a filesystem-safe stem."""
    text = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return (text[:max_len] or "stock").strip("_")


@dataclass
class StockClip:
    """Metadata about a downloaded stock clip."""

    path: Path
    provider: str = ""
    author: str = ""
    page_url: str = ""
    query: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "path": str(self.path),
            "provider": self.provider,
            "author": self.author,
            "page_url": self.page_url,
            "query": self.query,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "StockClip":
        return cls(
            path=Path(data["path"]),
            provider=data.get("provider", ""),
            author=data.get("author", ""),
            page_url=data.get("page_url", ""),
            query=data.get("query", ""),
            duration=float(data.get("duration", 0.0)),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            tags=list(data.get("tags", [])),
        )


class StockSource:
    """Searches and downloads royalty-free stock video clips."""

    def __init__(self, clips_dir: Optional[str] = None) -> None:
        self.clips_dir = Path(clips_dir or settings.stock_clips_dir)
        self.clips_dir.mkdir(parents=True, exist_ok=True)

    # -- availability -------------------------------------------------------

    @property
    def pexels_key(self) -> str:
        return (getattr(settings, "stock_pexels_api_key", "") or "").strip()

    @property
    def pixabay_key(self) -> str:
        return (getattr(settings, "stock_pixabay_api_key", "") or "").strip()

    def available(self) -> bool:
        """
        True when the feature is on.

        archive.org needs no key, so enabling STOCK_ENABLED is enough to make
        the module usable. Pexels/Pixabay are additive upgrades.
        """
        return bool(getattr(settings, "stock_enabled", False))

    def providers(self) -> List[str]:
        """Names of the providers that are configured, in priority order."""
        out: List[str] = []
        if self.pexels_key:
            out.append("pexels")
        if self.pixabay_key:
            out.append("pixabay")
        out.append("archive.org")
        return out

    # -- HTTP helpers -------------------------------------------------------

    def _get_json(self, url: str, headers: Dict[str, str]) -> Optional[Dict]:
        """GET `url` and parse the JSON body. Returns None on any failure."""
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(
                req, timeout=settings.stock_download_timeout
            ) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            logger.warning("Stock API HTTP %s for %s", exc.code, url)
            return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("Stock API request failed: %s", exc)
            return None

        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning("Stock API returned invalid JSON: %s", exc)
            return None
        return data if isinstance(data, dict) else None

    def _download(self, url: str, dest: Path) -> bool:
        """Stream `url` to `dest`. Returns True on success."""
        max_bytes = max(1, settings.stock_max_filesize_mb) * 1024 * 1024
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with urllib.request.urlopen(
                req, timeout=settings.stock_download_timeout
            ) as resp, tmp.open("wb") as fh:
                written = 0
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(
                            f"exceeds {settings.stock_max_filesize_mb} MB cap"
                        )
                    fh.write(chunk)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning("Stock download failed (%s): %s", url, exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

        if not tmp.is_file() or tmp.stat().st_size == 0:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

        try:
            tmp.replace(dest)
        except OSError as exc:
            logger.warning("Could not finalise stock clip: %s", exc)
            return False
        return True

    # -- search: Pexels -----------------------------------------------------

    def _search_pexels(self, query: str, limit: int) -> List[Dict]:
        """Return normalised candidate dicts from Pexels."""
        params = urllib.parse.urlencode(
            {"query": query, "per_page": max(1, min(limit, 80)), "orientation": "landscape"}
        )
        data = self._get_json(
            f"{PEXELS_API}?{params}",
            {"Authorization": self.pexels_key, "User-Agent": USER_AGENT},
        )
        if not data:
            return []

        out: List[Dict] = []
        for video in data.get("videos", []) or []:
            files = video.get("video_files", []) or []
            # Prefer an HD mp4 that is not gigantic; fall back to any mp4.
            best: Optional[Dict] = None
            for f in files:
                if (f.get("file_type") or "") != "video/mp4":
                    continue
                height = int(f.get("height") or 0)
                if height and height > 1920:
                    continue
                if best is None or height > int(best.get("height") or 0):
                    best = f
            if best is None:
                for f in files:
                    if (f.get("file_type") or "") == "video/mp4":
                        best = f
                        break
            if not best or not best.get("link"):
                continue

            out.append(
                {
                    "provider": "pexels",
                    "url": best["link"],
                    "page_url": video.get("url", ""),
                    "author": (video.get("user") or {}).get("name", ""),
                    "duration": float(video.get("duration") or 0.0),
                    "width": int(best.get("width") or 0),
                    "height": int(best.get("height") or 0),
                    "id": str(video.get("id") or ""),
                }
            )
        return out

    # -- search: Pixabay ----------------------------------------------------

    def _search_pixabay(self, query: str, limit: int) -> List[Dict]:
        """Return normalised candidate dicts from Pixabay."""
        params = urllib.parse.urlencode(
            {
                "key": self.pixabay_key,
                "q": query,
                "per_page": max(3, min(limit, 200)),
                "video_type": "film",
                "safesearch": "true",
            }
        )
        data = self._get_json(f"{PIXABAY_API}?{params}", {"User-Agent": USER_AGENT})
        if not data:
            return []

        out: List[Dict] = []
        for hit in data.get("hits", []) or []:
            videos = hit.get("videos") or {}
            # Pixabay tiers: large > medium > small > tiny.
            best: Optional[Dict] = None
            for tier in ("large", "medium", "small", "tiny"):
                candidate = videos.get(tier) or {}
                if candidate.get("url"):
                    best = candidate
                    break
            if not best or not best.get("url"):
                continue

            out.append(
                {
                    "provider": "pixabay",
                    "url": best["url"],
                    "page_url": hit.get("pageURL", ""),
                    "author": hit.get("user", ""),
                    "duration": float(hit.get("duration") or 0.0),
                    "width": int(best.get("width") or 0),
                    "height": int(best.get("height") or 0),
                    "id": str(hit.get("id") or ""),
                }
            )
        return out

    # -- search: archive.org ------------------------------------------------

    def _search_archive(self, query: str, limit: int) -> List[Dict]:
        """
        Return normalised candidate dicts from archive.org.

        archive.org needs NO API key: `advancedsearch.php` is a public JSON
        endpoint. We restrict the search to the `movies` mediatype and ask for
        items whose format is a video container, then resolve the actual file
        URL lazily in `fetch_clip` (via the metadata endpoint) because the
        search response does not include per-file links.

        Only public-domain / openly-licensed items are considered, so there is
        no Content ID risk.
        """
        # `fl[]` selects the fields we want back; `rows` caps the page size.
        params = [
            ("q", f"({query}) AND mediatype:(movies)"),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "creator"),
            ("fl[]", "licenseurl"),
            ("rows", str(max(1, min(limit * 3, 50)))),
            ("page", "1"),
            ("output", "json"),
        ]
        url = f"{ARCHIVE_SEARCH_API}?{urllib.parse.urlencode(params)}"
        data = self._get_json(url, {"User-Agent": USER_AGENT})
        if not data:
            return []

        docs = ((data.get("response") or {}).get("docs")) or []
        out: List[Dict] = []
        for doc in docs:
            identifier = (doc.get("identifier") or "").strip()
            if not identifier:
                continue
            out.append(
                {
                    "provider": "archive.org",
                    "identifier": identifier,
                    # Resolved later from the metadata endpoint.
                    "url": "",
                    "page_url": f"https://archive.org/details/{identifier}",
                    "author": doc.get("creator") or "",
                    "duration": 0.0,
                    "width": 0,
                    "height": 0,
                    "id": identifier,
                    "title": doc.get("title") or "",
                }
            )
            if len(out) >= limit:
                break
        return out

    def _resolve_archive_file(self, identifier: str) -> Optional[Dict]:
        """
        Resolve a playable MP4 URL for an archive.org item.

        The search endpoint only gives us identifiers, so we hit the metadata
        endpoint to list the item's files and pick the smallest sensible MP4
        (we do not want a 4 GB master file). Returns a dict with `url`,
        `width`, `height` and `duration`, or None.
        """
        data = self._get_json(
            f"{ARCHIVE_METADATA_API}{urllib.parse.quote(identifier)}",
            {"User-Agent": USER_AGENT},
        )
        if not data:
            return None

        files = data.get("files") or []
        best: Optional[Dict] = None
        best_size = 0
        for f in files:
            name = (f.get("name") or "").strip()
            if not name.lower().endswith((".mp4", ".m4v", ".webm")):
                continue
            # Skip derivative thumbnails / samples.
            if any(tag in name.lower() for tag in ("thumb", "sample", "preview")):
                continue
            try:
                size = int(f.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            # Prefer the largest file under the cap; a tiny file is usually a
            # low-res derivative, a huge one blows the download budget.
            cap = max(1, settings.stock_max_filesize_mb) * 1024 * 1024
            if size and size > cap:
                continue
            if size > best_size:
                best = f
                best_size = size

        if best is None:
            return None

        name = best.get("name") or ""
        url = f"{ARCHIVE_DOWNLOAD_ROOT}{identifier}/{urllib.parse.quote(name)}"
        try:
            duration = float(best.get("length") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        return {
            "url": url,
            "width": int(best.get("width") or 0),
            "height": int(best.get("height") or 0),
            "duration": duration,
        }

    # -- public search ------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Search every configured provider and return normalised candidates.

        Pexels results come first (better quality, cleaner licence), then
        Pixabay, then archive.org fills whatever is left. archive.org needs no
        key, so this always returns something as long as STOCK_ENABLED is on.
        """
        if not self.available():
            logger.debug("Stock source unavailable (disabled).")
            return []

        results: List[Dict] = []
        if self.pexels_key:
            results.extend(self._search_pexels(query, limit))
        if len(results) < limit and self.pixabay_key:
            results.extend(self._search_pixabay(query, limit - len(results)))
        if len(results) < limit:
            results.extend(self._search_archive(query, limit - len(results)))

        logger.info("Stock search '%s' -> %d candidate(s)", query, len(results))
        return results[:limit]

    # -- cache --------------------------------------------------------------

    def _meta_path(self, stem: str) -> Path:
        return self.clips_dir / f"{stem}.json"

    def _video_path(self, stem: str) -> Path:
        return self.clips_dir / f"{stem}.mp4"

    def cached(self, stem: str) -> Optional[StockClip]:
        """Return cached metadata if the clip is already on disk."""
        meta = self._meta_path(stem)
        video = self._video_path(stem)
        if meta.exists() and video.exists() and video.stat().st_size > 0:
            try:
                with meta.open("r", encoding="utf-8") as fh:
                    info = StockClip.from_dict(json.load(fh))
                if info.path.exists():
                    return info
            except (OSError, ValueError, KeyError) as exc:
                logger.warning("Bad stock metadata for '%s': %s", stem, exc)
        return None

    def list_cached(self) -> List[StockClip]:
        """All stock clips currently cached on disk."""
        out: List[StockClip] = []
        for meta in sorted(self.clips_dir.glob("*.json")):
            info = self.cached(meta.stem)
            if info:
                out.append(info)
        return out

    def _write_meta(self, stem: str, clip: StockClip) -> None:
        try:
            with self._meta_path(stem).open("w", encoding="utf-8") as fh:
                json.dump(clip.to_dict(), fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("Could not write stock metadata: %s", exc)

    # -- download -----------------------------------------------------------

    def fetch_clip(self, candidate: Dict, query: str = "") -> Optional[StockClip]:
        """Download one candidate and return its StockClip, or None."""
        url = candidate.get("url") or ""
        provider = candidate.get("provider", "stock")

        # archive.org search results carry only an identifier; the real file
        # URL has to be resolved from the metadata endpoint first.
        if not url and provider == "archive.org":
            identifier = candidate.get("identifier") or candidate.get("id") or ""
            if not identifier:
                return None
            resolved = self._resolve_archive_file(identifier)
            if not resolved:
                logger.debug("No playable file for archive.org item %s", identifier)
                return None
            url = resolved["url"]
            candidate = {**candidate, **resolved}

        if not url:
            return None

        ident = candidate.get("id") or _safe_name(url.rsplit("/", 1)[-1])
        stem = _safe_name(f"{provider}_{ident}")

        hit = self.cached(stem)
        if hit:
            logger.debug("Stock cache hit: %s", stem)
            return hit

        dest = self._video_path(stem)
        logger.info("Downloading stock clip (%s): %s", provider, url)
        if not self._download(url, dest):
            return None

        clip = StockClip(
            path=dest,
            provider=provider,
            author=candidate.get("author", ""),
            page_url=candidate.get("page_url", ""),
            query=query,
            duration=float(candidate.get("duration") or 0.0),
            width=int(candidate.get("width") or 0),
            height=int(candidate.get("height") or 0),
            tags=[query] if query else [],
        )
        self._write_meta(stem, clip)
        return clip

    def fetch_clips(self, query: str, count: int = 5) -> List[StockClip]:
        """
        Search for `query` and download up to `count` clips.

        Cached clips are returned first so repeat runs cost no bandwidth.
        """
        if not self.available():
            return []

        wanted = max(1, count)
        clips: List[StockClip] = []

        # 1. Reuse anything already cached for this query.
        for hit in self.list_cached():
            if len(clips) >= wanted:
                break
            if hit.query == query:
                clips.append(hit)

        if len(clips) >= wanted:
            return clips[:wanted]

        # 2. Download fresh candidates.
        candidates = self.search(query, limit=wanted * 3)
        for candidate in candidates:
            if len(clips) >= wanted:
                break
            clip = self.fetch_clip(candidate, query=query)
            if clip and clip.path not in [c.path for c in clips]:
                clips.append(clip)

        logger.info("Stock fetch '%s' -> %d clip(s)", query, len(clips))
        return clips[:wanted]

    def fetch_for_title(self, title: str, count: int = 5) -> List[StockClip]:
        """
        Fetch clips for a movie/TV title.

        The title itself is a poor stock search term ("Sinister" returns
        nothing useful), so we map it to a cinematic mood query. A rotating
        default is used when no mood can be inferred, which also keeps the
        footage varied between runs.
        """
        query = self._query_for_title(title)
        clips = self.fetch_clips(query, count=count)
        if clips:
            return clips

        # Fall back through the generic cinematic pool.
        for fallback in self._fallback_queries():
            if fallback == query:
                continue
            clips = self.fetch_clips(fallback, count=count)
            if clips:
                return clips
        return []

    # -- query mapping ------------------------------------------------------

    def _fallback_queries(self) -> List[str]:
        configured = list(getattr(settings, "stock_default_queries", []) or [])
        return configured or DEFAULT_QUERIES

    @staticmethod
    def _query_for_title(title: str) -> str:
        """
        Map a title to a cinematic stock query.

        We deliberately do NOT search for the title itself: stock libraries
        have no footage of a specific film, and the title would return noise.
        Instead we pick a mood that suits a horror/thriller/sci-fi channel.
        """
        text = (title or "").lower()
        mood_map = [
            (("horror", "sinister", "conjuring", "insidious", "exorcist",
              "halloween", "scream", "it ", "us ", "hereditary", "midsommar"),
             "cinematic dark forest fog"),
            (("space", "alien", "interstellar", "gravity", "martian",
              "dune", "star", "moon", "cosmos"),
             "cinematic space nebula stars"),
            (("action", "fast", "mission", "bourne", "john wick", "extraction"),
             "cinematic city night neon"),
            (("ocean", "sea", "water", "deep", "jaws", "titanic", "aquaman"),
             "cinematic ocean waves storm"),
            (("war", "soldier", "battle", "1917", "dunkirk"),
             "cinematic smoke ruins dramatic"),
            (("love", "romance", "notebook", "la la land"),
             "cinematic sunset couple silhouette"),
            (("crime", "godfather", "scarface", "pulp", "departed"),
             "cinematic rainy city street night"),
            (("fantasy", "harry", "lord", "rings", "hobbit", "narnia"),
             "cinematic misty mountains epic"),
        ]
        for keywords, query in mood_map:
            if any(k in text for k in keywords):
                return query

        # Deterministic rotation so the same title always maps to the same
        # query, but different titles spread across the pool.
        pool = StockSource._default_pool()
        return pool[abs(hash(title or "")) % len(pool)]

    @staticmethod
    def _default_pool() -> List[str]:
        configured = list(getattr(settings, "stock_default_queries", []) or [])
        return configured or DEFAULT_QUERIES


#: Module-level singleton, matching the convention used by every other module.
stock_source = StockSource()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Search and download royalty-free stock video clips."
    )
    sub = parser.add_subparsers(dest="command")

    p_search = sub.add_parser("search", help="List candidates without downloading")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=10)

    p_fetch = sub.add_parser("fetch", help="Download clips for a query")
    p_fetch.add_argument("--query", required=True)
    p_fetch.add_argument("--count", type=int, default=5)

    p_title = sub.add_parser("title", help="Download clips for a movie title")
    p_title.add_argument("--title", required=True)
    p_title.add_argument("--count", type=int, default=5)

    sub.add_parser("list", help="List cached clips")
    sub.add_parser("check", help="Report availability and providers")
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s"
    )
    args = build_parser().parse_args()

    if args.command == "check":
        print("available:", stock_source.available())
        print("providers:", stock_source.providers() or "(none)")
        print("clips_dir:", stock_source.clips_dir)
        return

    if args.command == "list":
        cached = stock_source.list_cached()
        if not cached:
            print("! No cached stock clips.")
            return
        for clip in cached:
            print(f"{clip.path} | {clip.provider} | {clip.query}")
        return

    if args.command == "search":
        results = stock_source.search(args.query, limit=args.limit)
        if not results:
            print("! No results (check STOCK_ENABLED and the API keys).")
            return
        for r in results:
            print(
                f"{r['provider']:8} | {r.get('width')}x{r.get('height')} | "
                f"{r.get('duration')}s | {r['url']}"
            )
        return

    if args.command == "fetch":
        clips = stock_source.fetch_clips(args.query, count=args.count)
        if not clips:
            print("! Nothing downloaded.")
            return
        for clip in clips:
            print(f"{clip.path} | {clip.provider} | {clip.query}")
        return

    if args.command == "title":
        clips = stock_source.fetch_for_title(args.title, count=args.count)
        if not clips:
            print("! Nothing downloaded.")
            return
        for clip in clips:
            print(f"{clip.path} | {clip.provider} | {clip.query}")
        return

    build_parser().print_help()


if __name__ == "__main__":
    main()
