"""
trailer_downloader.py
=====================
Downloads OFFICIAL movie/TV trailers from YouTube using yt-dlp, so the video
composer can cut short clips out of them.

WHY TRAILERS?
    Trailers are published by the studios themselves for promotional purposes.
    Re-using short excerpts with commentary/transformation is the most defensible
    "fair use"-style position available without paying for a licence. It is NOT
    risk-free: YouTube's Content ID is automated and may still claim the video.
    See README for the full risk discussion.

DESIGN
    * yt-dlp is used as a SUBPROCESS (not imported) so a missing/broken install
      degrades gracefully instead of crashing the whole pipeline.
    * Downloads are cached on disk: a trailer is fetched once and reused.
    * A metadata sidecar (.json) records the source URL, title and channel so
      attribution can be added to the description.
    * Everything is bounded: max duration, max filesize, max resolution.

USAGE (CLI)
    python trailer_downloader.py --url "https://www.youtube.com/watch?v=XXXX"
    python trailer_downloader.py --search "Sinister official trailer"
    python trailer_downloader.py --list

USAGE (code)
    from trailer_downloader import trailer_downloader
    path = trailer_downloader.fetch("https://www.youtube.com/watch?v=XXXX")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

#: Hard ceiling on a downloaded trailer. Trailers are ~2-3 min; anything much
#: longer is probably a full movie upload and we do not want it.
MAX_DURATION_SECONDS = 600

#: Cap the file size so a rogue 4K download cannot fill the VPS disk.
MAX_FILESIZE = "150M"

#: 1080p is plenty: the output is a 1080x1920 vertical crop anyway.
MAX_HEIGHT = 1080

#: yt-dlp format selector. Prefer mp4/h264 so FFmpeg never needs a transcode
#: just to read the file.
FORMAT_SELECTOR = (
    f"bestvideo[height<={MAX_HEIGHT}][ext=mp4]+bestaudio[ext=m4a]/"
    f"best[height<={MAX_HEIGHT}][ext=mp4]/best[height<={MAX_HEIGHT}]"
)

#: A browser-like UA reduces the chance of YouTube serving a bot-check page.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

#: Datacenter IPs (VPS) are frequently challenged by YouTube with
#: "Sign in to confirm you're not a bot". Passing a Netscape-format cookies.txt
#: exported from a logged-in browser is the standard, reliable workaround.
#: Set TRAILER_COOKIES_FILE in .env to enable it.
#:
#: IMPORTANT: cookies are a *last resort*, not the default. In practice a
#: cookies.txt exported from a browser session that does not match the VPS
#: IP/region makes things WORSE: YouTube answers every player request with
#: "The page needs to be reloaded" or returns storyboard-only formats, while
#: the same request without cookies succeeds. So we first try the anonymous
#: clients (which work fine from most VPS IPs) and only fall back to cookies
#: when every anonymous client has failed.
COOKIES_FILE = os.getenv("TRAILER_COOKIES_FILE", "").strip()

#: Optional: route yt-dlp through a proxy (e.g. a residential proxy) when the
#: VPS IP is blocked outright. Set TRAILER_PROXY in .env to enable it.
PROXY_URL = os.getenv("TRAILER_PROXY", "").strip()

#: Player clients tried in order. The first three need no authentication and
#: are the ones that actually serve real video streams from a datacenter IP.
#: `web_safari`/`mweb` are kept as a middle tier because they sometimes work
#: when the others are rate-limited. The final entry re-tries the default
#: client *with* cookies, which is the only place cookies are used.
PLAYER_CLIENTS: List[str] = [
    "android",
    "tv_embedded",
    "ios",
    "web_safari",
    "mweb",
]


def _auth_args(use_cookies: bool = False) -> List[str]:
    """
    Build the yt-dlp auth-related flags shared by probe and download.

    Cookies are only attached when ``use_cookies`` is true, so the anonymous
    attempts stay anonymous. A proxy, when configured, is always applied.
    """
    args: List[str] = []
    if use_cookies and COOKIES_FILE and Path(COOKIES_FILE).is_file():
        args += ["--cookies", COOKIES_FILE]
    if PROXY_URL:
        args += ["--proxy", PROXY_URL]
    return args


def _client_args(client: Optional[str]) -> List[str]:
    """Build the ``--extractor-args`` flag selecting a YouTube player client."""
    if not client:
        return []
    return ["--extractor-args", f"youtube:player_client={client}"]


def _attempt_plan() -> List[Tuple[Optional[str], bool]]:
    """
    Return the ordered list of ``(player_client, use_cookies)`` attempts.

    Anonymous clients come first. Cookies are appended as a final attempt only
    when a cookies file is actually configured, so a broken cookies file can
    never prevent the working anonymous path from being used.
    """
    plan: List[Tuple[Optional[str], bool]] = [
        (client, False) for client in PLAYER_CLIENTS
    ]
    plan.append((None, False))  # yt-dlp's own default client, no cookies
    if COOKIES_FILE and Path(COOKIES_FILE).is_file():
        plan.append((None, True))  # last resort: default client + cookies
    return plan


def _safe_name(text: str, max_len: int = 80) -> str:
    """Turn an arbitrary title into a filesystem-safe stem."""
    text = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return (text[:max_len] or "clip").strip("_")


@dataclass
class TrailerInfo:
    """Metadata about a downloaded trailer."""

    path: Path
    title: str = ""
    channel: str = ""
    url: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "path": str(self.path),
            "title": self.title,
            "channel": self.channel,
            "url": self.url,
            "duration": self.duration,
            "width": self.width,
            "height": self.height,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "TrailerInfo":
        return cls(
            path=Path(data["path"]),
            title=data.get("title", ""),
            channel=data.get("channel", ""),
            url=data.get("url", ""),
            duration=float(data.get("duration", 0.0)),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            tags=list(data.get("tags", [])),
        )


class TrailerDownloader:
    """Downloads and caches official trailers via yt-dlp."""

    def __init__(self, clips_dir: Optional[str] = None) -> None:
        self.clips_dir = Path(clips_dir or settings.trailer_clips_dir)
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self._ytdlp: Optional[str] = None
        self._checked = False

    # -- availability -------------------------------------------------------

    def _find_ytdlp(self) -> Optional[str]:
        """Locate the yt-dlp executable (PATH, then python -m)."""
        if self._checked:
            return self._ytdlp
        self._checked = True

        exe = shutil.which("yt-dlp")
        if exe:
            self._ytdlp = exe
            logger.debug("Found yt-dlp at %s", exe)
            return self._ytdlp

        # Fall back to the module form: `python -m yt_dlp`.
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "yt_dlp", "--version"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode == 0:
                self._ytdlp = f"{sys.executable} -m yt_dlp"
                logger.debug("Found yt-dlp as a Python module")
                return self._ytdlp
        except (OSError, subprocess.SubprocessError):
            pass

        logger.warning("yt-dlp is not installed; trailer downloads are disabled.")
        return None

    def available(self) -> bool:
        """True when yt-dlp can be invoked."""
        return self._find_ytdlp() is not None

    # -- cache --------------------------------------------------------------

    def _meta_path(self, stem: str) -> Path:
        return self.clips_dir / f"{stem}.json"

    def _video_path(self, stem: str) -> Path:
        return self.clips_dir / f"{stem}.mp4"

    def cached(self, stem: str) -> Optional[TrailerInfo]:
        """Return cached metadata if the trailer is already on disk."""
        meta = self._meta_path(stem)
        video = self._video_path(stem)
        if meta.exists() and video.exists() and video.stat().st_size > 0:
            try:
                with meta.open("r", encoding="utf-8") as fh:
                    info = TrailerInfo.from_dict(json.load(fh))
                if info.path.exists():
                    logger.debug("Cache hit for trailer '%s'", stem)
                    return info
            except (OSError, ValueError, KeyError) as exc:
                logger.warning("Bad trailer metadata for '%s': %s", stem, exc)
        return None

    def list_cached(self) -> List[TrailerInfo]:
        """All trailers currently cached on disk."""
        out: List[TrailerInfo] = []
        for meta in sorted(self.clips_dir.glob("*.json")):
            info = self.cached(meta.stem)
            if info:
                out.append(info)
        return out

    # -- download -----------------------------------------------------------

    def _probe(self, path: Path) -> Dict:
        """Read duration/resolution with ffprobe (best effort)."""
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return {}
        cmd = [
            ffprobe, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                return {}
            data = json.loads(proc.stdout or "{}")
            stream = (data.get("streams") or [{}])[0]
            fmt = data.get("format") or {}
            return {
                "width": int(stream.get("width") or 0),
                "height": int(stream.get("height") or 0),
                "duration": float(fmt.get("duration") or 0.0),
            }
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.debug("ffprobe failed for %s: %s", path, exc)
            return {}

    def fetch(
        self,
        url: str,
        stem: Optional[str] = None,
        force: bool = False,
    ) -> Optional[TrailerInfo]:
        """
        Download a trailer (or return the cached copy).

        Args:
            url:   YouTube watch URL of the OFFICIAL trailer.
            stem:  Optional filename stem; derived from the title otherwise.
            force: Re-download even if a cached copy exists.

        Returns:
            TrailerInfo on success, None on failure (never raises).
        """
        if not url:
            logger.error("fetch() called without a URL")
            return None

        ytdlp = self._find_ytdlp()
        if not ytdlp:
            return None

        # We need the title before we know the stem, so ask yt-dlp first.
        title = ""
        channel = ""
        if not stem:
            meta = self._dump_json(ytdlp, url)
            if meta:
                title = meta.get("title") or ""
                channel = meta.get("channel") or meta.get("uploader") or ""
            stem = _safe_name(title or url.rsplit("=", 1)[-1])

        if not force:
            hit = self.cached(stem)
            if hit:
                return hit

        out_tmpl = str(self.clips_dir / f"{stem}.%(ext)s")

        logger.info("Downloading trailer: %s", url)
        last_error = "unknown error"
        downloaded = False

        # Try each (client, cookies) combination until one yields a file.
        # Anonymous clients are attempted first; cookies are a last resort.
        for client, use_cookies in _attempt_plan():
            cmd = [
                *ytdlp.split(),
                "--no-playlist",
                "--no-warnings",
                "--no-progress",
                "--max-filesize", MAX_FILESIZE,
                "--match-filter", f"duration<={MAX_DURATION_SECONDS}",
                "--format", FORMAT_SELECTOR,
                "--merge-output-format", "mp4",
                "--user-agent", USER_AGENT,
                *_client_args(client),
                *_auth_args(use_cookies),
                "--output", out_tmpl,
                "--print", "after_move:filepath",
                url,
            ]
            label = f"client={client or 'default'} cookies={use_cookies}"
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    timeout=settings.trailer_download_timeout,
                )
            except subprocess.TimeoutExpired:
                last_error = f"timed out after {settings.trailer_download_timeout}s"
                logger.warning("Attempt failed (%s): %s", label, last_error)
                continue
            except (OSError, subprocess.SubprocessError) as exc:
                last_error = f"yt-dlp failed to start: {exc}"
                logger.warning("Attempt failed (%s): %s", label, last_error)
                continue

            if proc.returncode == 0:
                logger.info("Download succeeded (%s)", label)
                downloaded = True
                break

            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            last_error = tail[-1] if tail else "unknown error"
            logger.warning("Attempt failed (%s): %s", label, last_error)

        if not downloaded:
            logger.error("All download attempts failed for %s: %s", url, last_error)
            return None

        video = self._video_path(stem)
        if not video.exists():
            # yt-dlp may have produced a different container; find it.
            candidates = [
                p for p in self.clips_dir.glob(f"{stem}.*")
                if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
            ]
            if not candidates:
                logger.error("yt-dlp reported success but no file was written")
                return None
            video = candidates[0]

        probe = self._probe(video)
        info = TrailerInfo(
            path=video,
            title=title or stem,
            channel=channel,
            url=url,
            duration=probe.get("duration", 0.0),
            width=probe.get("width", 0),
            height=probe.get("height", 0),
        )

        try:
            with self._meta_path(stem).open("w", encoding="utf-8") as fh:
                json.dump(info.to_dict(), fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("Could not write trailer metadata: %s", exc)

        size_mb = video.stat().st_size / (1024 * 1024)
        logger.info("Trailer ready: %s (%.1f MB, %.0fs)",
                    video.name, size_mb, info.duration)
        return info

    def _dump_json(self, ytdlp: str, url: str) -> Dict:
        """Fetch metadata only (no download) so we can derive a filename."""
        cmd = [
            *ytdlp.split(),
            "--no-playlist", "--no-warnings", "--skip-download",
            "--dump-single-json", "--user-agent", USER_AGENT,
            *_auth_args(False), url,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                return {}
            return json.loads(proc.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            logger.debug("Metadata probe failed: %s", exc)
            return {}

    def search(self, query: str, limit: int = 5) -> List[Dict]:
        """
        Search YouTube and return candidate trailers (metadata only).

        The caller is expected to pick an OFFICIAL trailer (studio channel).
        """
        ytdlp = self._find_ytdlp()
        if not ytdlp:
            return []

        cmd = [
            *ytdlp.split(),
            f"ytsearch{limit}:{query}",
            "--no-playlist", "--no-warnings", "--skip-download",
            "--dump-json", "--user-agent", USER_AGENT,
            *_auth_args(False),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("yt-dlp search failed: %s", exc)
            return []

        results: List[Dict] = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            results.append({
                "id": data.get("id", ""),
                "title": data.get("title", ""),
                "channel": data.get("channel") or data.get("uploader") or "",
                "duration": data.get("duration") or 0,
                "url": data.get("webpage_url") or "",
            })
        return results


#: Module-level singleton, mirroring the other subsystems.
trailer_downloader = TrailerDownloader()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_fetch(args: argparse.Namespace) -> int:
    info = trailer_downloader.fetch(args.url, stem=args.stem, force=args.force)
    if not info:
        print("! Download failed (see logs above).")
        return 1
    print(f"OK  {info.path}")
    print(f"    title    : {info.title}")
    print(f"    channel  : {info.channel}")
    print(f"    duration : {info.duration:.0f}s")
    print(f"    size     : {info.path.stat().st_size / 1048576:.1f} MB")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    results = trailer_downloader.search(args.search, limit=args.limit)
    if not results:
        print("! No results (is yt-dlp installed?).")
        return 1
    for i, r in enumerate(results, 1):
        mins, secs = divmod(int(r["duration"]), 60)
        print(f"{i}. {r['title']}")
        print(f"   channel : {r['channel']}")
        print(f"   length  : {mins}:{secs:02d}")
        print(f"   url     : {r['url']}")
        print()
    return 0


def _cmd_list(_: argparse.Namespace) -> int:
    items = trailer_downloader.list_cached()
    if not items:
        print("No trailers cached yet.")
        return 0
    total = 0.0
    for info in items:
        size = info.path.stat().st_size / 1048576
        total += size
        print(f"- {info.path.name}  ({size:.1f} MB, {info.duration:.0f}s)")
        print(f"    {info.title}  [{info.channel}]")
    print(f"\n{len(items)} trailer(s), {total:.1f} MB total")
    return 0


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Download official trailers for the Cinevo video pipeline."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="Download a trailer by URL.")
    p_fetch.add_argument("--url", required=True, help="YouTube watch URL.")
    p_fetch.add_argument("--stem", default=None, help="Override the filename stem.")
    p_fetch.add_argument("--force", action="store_true", help="Re-download.")
    p_fetch.set_defaults(func=_cmd_fetch)

    p_search = sub.add_parser("search", help="Search YouTube for trailers.")
    p_search.add_argument("--search", required=True, help="Search query.")
    p_search.add_argument("--limit", type=int, default=5)
    p_search.set_defaults(func=_cmd_search)

    p_list = sub.add_parser("list", help="List cached trailers.")
    p_list.set_defaults(func=_cmd_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
