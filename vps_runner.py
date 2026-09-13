"""
vps_runner.py
=============
Orchestrator for the VPS trailer pipeline. This is the entry point you run on
the VPS (manually, via cron, or via the systemd timer installed by deploy.sh).

PIPELINE
--------
    1. PLAN      pick the titles to produce today
    2. SCRIPT    DeepSeek generates the hook / caption / hashtags
    3. DOWNLOAD  yt-dlp fetches the OFFICIAL trailer for each title
    4. CLIP      cut + transform short clips (Ken Burns / grade / mirror)
    5. COMPOSE   stitch clips + hook overlay + voiceover + music
    6. PUBLISH   upload to YouTube Shorts (respecting every safety gate)

WHY A VPS?
----------
    * No shared-IP download bans: yt-dlp runs from your own address.
    * No 6-hour job limit: a full render can take as long as it needs.
    * Persistent disk: trailers and clips are cached between runs.
    * Full control over FFmpeg, fonts and codecs.

SAFETY GATES (all must pass before anything is uploaded)
--------------------------------------------------------
    * TRAILER_ENABLED=true          master kill-switch for this pipeline
    * CONTENT_ENABLED=true          shared content kill-switch
    * CONTENT_AUTO_PUBLISH=true     explicit opt-in to upload
    * CONTENT_DAILY_PUBLISH_LIMIT   hard daily cap
    * --no-publish / --dry-run      per-run overrides

COPYRIGHT WARNING
-----------------
    Re-using trailer footage is NOT risk-free. YouTube's Content ID is
    automated and may claim or block the upload. The transformations in
    clip_processor.py reduce the chance of a match but do not eliminate it.
    Read the README section on risk before enabling auto-publish.

USAGE
-----
    # Safe preview: build the video, never upload
    python vps_runner.py --no-publish

    # Show what would happen, touch nothing
    python vps_runner.py --dry-run

    # Full auto run (respects all gates)
    python vps_runner.py

    # Force a specific title
    python vps_runner.py --title "Sinister" --no-publish
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Make stdout UTF-8 safe on Windows (cp1252 would crash on emoji/Arabic).
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from clip_processor import clip_processor
from config import settings
from content_generator import content_generator
from content_planner import content_planner
from publish_queue import publish_queue
from trailer_downloader import trailer_downloader
from video_composer import video_composer
from video_maker import video_maker
from youtube_uploader import youtube_uploader

logger = logging.getLogger(__name__)


class VpsRunner:
    """Runs the trailer pipeline end to end, with every safety gate enforced."""

    def __init__(self, dry_run: bool = False, no_publish: bool = False) -> None:
        self.dry_run = dry_run
        self.no_publish = no_publish

    # ------------------------------------------------------------------ #
    # Gate checks
    # ------------------------------------------------------------------ #
    def _gate_reason(self) -> Optional[str]:
        """Return a human-readable reason the pipeline is blocked, or None."""
        if not getattr(settings, "trailer_enabled", False):
            return "TRAILER_ENABLED is false."
        if not settings.content_enabled:
            return "CONTENT_ENABLED is false."
        if not trailer_downloader.available():
            return "yt-dlp is not available."
        if not clip_processor.available():
            return "FFmpeg is not available."
        if not video_composer.available():
            return "FFmpeg is not available for composing."
        return None

    # ------------------------------------------------------------------ #
    # Planning
    # ------------------------------------------------------------------ #
    def _pick_titles(self, count: int, forced: Optional[str]) -> List[str]:
        """Choose which titles to produce this run."""
        if forced:
            return [forced]

        # Explicit TRAILER_TITLES wins over the generic planner pool.
        configured = list(getattr(settings, "trailer_titles", []) or [])
        if configured:
            queued = set(publish_queue.titles())
            fresh = [t for t in configured if t not in queued]
            return (fresh or configured)[:count]

        plan = content_planner.build_daily_plan(
            exclude_titles=publish_queue.titles()
        )
        titles = [entry["title"] for entry in plan][:count]
        if not titles:
            titles = list(content_planner.title_pool)[:count]
        return titles

    # ------------------------------------------------------------------ #
    # Source resolution
    # ------------------------------------------------------------------ #
    def _resolve_source(self, title: str) -> Optional[Path]:
        """
        Find a trailer file for `title`.

        Order of preference:
          1. An explicit URL from TRAILER_SOURCE_URLS (round-robin by title).
          2. A YouTube search for "<title> official trailer".
          3. An already-cached trailer on disk.
        """
        urls = list(getattr(settings, "trailer_source_urls", []) or [])

        if urls:
            # Deterministic pick so the same title always maps to the same URL.
            index = abs(hash(title)) % len(urls)
            url = urls[index]
            logger.info("Using configured trailer URL for '%s': %s", title, url)
            info = trailer_downloader.fetch(url)
            if info:
                return info.path

        # Try a search.
        results = trailer_downloader.search(f"{title} official trailer", limit=3)
        for result in results:
            url = result.get("url") or ""
            if not url:
                continue
            logger.info("Trying trailer for '%s': %s", title, url)
            info = trailer_downloader.fetch(url)
            if info:
                return info.path

        # Last resort: anything already cached.
        cached = trailer_downloader.list_cached()
        if cached:
            logger.info("Falling back to cached trailer: %s", cached[0].path)
            return cached[0].path

        logger.warning("No trailer source found for '%s'.", title)
        return None

    # ------------------------------------------------------------------ #
    # Voiceover
    # ------------------------------------------------------------------ #
    def _make_voiceover(self, pkg: Dict, work_dir: Path) -> Optional[Path]:
        """
        Generate a voiceover with edge-tts.

        Re-uses video_maker's implementation so the voice/rate settings stay
        in one place. Returns None when voice is disabled or edge-tts missing.
        """
        if not getattr(settings, "content_enable_voice", True):
            return None

        script = (pkg.get("script") or pkg.get("hook") or "").strip()
        if not script:
            return None

        try:
            # video_maker exposes a private helper; call it defensively so a
            # future refactor degrades to "no voiceover" instead of crashing.
            #
            # NOTE: video_maker._make_voiceover(text, out_path) returns a *bool*
            # (True on success) and writes the MP3 to `out_path`. It does NOT
            # return the path, so we must return `out` ourselves.
            maker = video_maker
            out = work_dir / "voiceover.mp3"
            ok = maker._make_voiceover(script, str(out))  # type: ignore[attr-defined]
            if ok and out.is_file() and out.stat().st_size > 0:
                return out
        except Exception as exc:  # noqa: BLE001 - never let audio break a run
            logger.warning("Voiceover generation failed: %s", exc)
        return None

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #
    def run(self, count: int = 1, forced_title: Optional[str] = None) -> int:
        """Run the pipeline. Returns the number of videos published."""
        reason = self._gate_reason()
        if reason:
            logger.warning("Trailer pipeline disabled: %s", reason)
            return 0

        titles = self._pick_titles(count, forced_title)
        if not titles:
            logger.warning("No titles available to produce.")
            return 0

        logger.info(
            "Trailer run: %d title(s) -> %s", len(titles), ", ".join(titles)
        )

        if self.dry_run:
            for title in titles:
                print(f"[DRY-RUN] would download + clip + compose: {title}")
            print(
                f"[DRY-RUN] auto_publish={settings.content_auto_publish} "
                f"privacy={settings.content_privacy} "
                f"daily_limit={settings.content_daily_publish_limit} "
                f"published_today={publish_queue.published_today()} "
                f"clips_per_video={settings.trailer_clips_per_video} "
                f"clip_seconds={settings.trailer_clip_seconds}"
            )
            return 0

        published = 0
        for title in titles:
            if self._produce_one(title):
                published += 1

        logger.info("Trailer run complete: %d video(s) published.", published)
        return published

    def _produce_one(self, title: str) -> bool:
        """Produce (and optionally publish) a single video. Returns published."""
        # 1) Script ------------------------------------------------------
        logger.info("Generating script for '%s'...", title)
        pkg = content_generator.generate(title)
        if not pkg:
            logger.error("Script generation failed for '%s'.", title)
            return False

        # 2) Source trailer ---------------------------------------------
        source = self._resolve_source(title)
        if not source:
            logger.warning("No trailer for '%s'; skipping.", title)
            return False

        # 3) Clips -------------------------------------------------------
        clips = clip_processor.process(source)
        if not clips:
            logger.warning(
                "No clips produced for '%s'; falling back to text-only video.", title
            )
            return self._fallback_text_video(pkg)

        # 4) Compose -----------------------------------------------------
        work_dir = Path(settings.content_video_dir) / "work"
        work_dir.mkdir(parents=True, exist_ok=True)

        voiceover = self._make_voiceover(pkg, work_dir)
        music_path = getattr(settings, "trailer_music_path", "") or ""
        music = Path(music_path) if music_path and Path(music_path).is_file() else None

        out_path = Path(settings.content_video_dir) / f"{_safe_stem(title)}.mp4"
        hook = pkg.get("hook") or ""

        composed = video_composer.compose(
            [c.path for c in clips],
            hook=hook,
            out_path=out_path,
            voiceover=voiceover,
            music=music,
        )
        if not composed:
            logger.warning(
                "Composition failed for '%s'; falling back to text-only video.", title
            )
            return self._fallback_text_video(pkg)

        # 5) Queue + publish --------------------------------------------
        if getattr(settings, "trailer_attribution", True):
            source_url = ""
            try:
                import json

                sidecar = Path(source).with_suffix(".json")
                if sidecar.is_file():
                    source_url = json.loads(
                        sidecar.read_text(encoding="utf-8")
                    ).get("url", "")
            except (OSError, ValueError):
                source_url = ""
            if source_url:
                pkg["caption"] = (
                    f"{pkg.get('caption', '')}\n\nTrailer source: {source_url}"
                ).strip()

        queue_id = publish_queue.add(pkg, status="ready")
        if queue_id is None:
            logger.warning("Could not queue '%s'.", title)
            return False

        if self.no_publish:
            logger.info(
                "Rendered '%s' -> %s (publishing disabled by --no-publish).",
                title,
                composed,
            )
            return False

        if not youtube_uploader.can_publish():
            logger.info(
                "Publishing skipped for '%s' (gate closed). Video kept at %s.",
                title,
                composed,
            )
            return False

        url = youtube_uploader.publish(str(composed), pkg, queue_id=queue_id)
        if url:
            logger.info("Published '%s' -> %s", title, url)
            return True
        return False

    def _fallback_text_video(self, pkg: Dict) -> bool:
        """
        Fall back to the original text-only video maker.

        This keeps the pipeline productive even when yt-dlp or the trailer
        download fails: we still ship a (100% original) Short.
        """
        if not video_maker.available():
            logger.warning("Text-only fallback unavailable (FFmpeg missing).")
            return False

        logger.info("Building text-only fallback video for '%s'.", pkg.get("title"))
        video_path = video_maker.build(pkg)
        if not video_path:
            return False

        queue_id = publish_queue.add(pkg, status="ready")
        if queue_id is None:
            return False

        if self.no_publish or not youtube_uploader.can_publish():
            logger.info("Fallback video kept at %s (not published).", video_path)
            return False

        url = youtube_uploader.publish(video_path, pkg, queue_id=queue_id)
        return bool(url)


def _safe_stem(text: str) -> str:
    """Filesystem-safe stem for the output video."""
    import re

    cleaned = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned[:60] or "short"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vps_runner",
        description=(
            "VPS trailer pipeline: download -> clip -> compose -> publish. "
            "Respects TRAILER_ENABLED / CONTENT_ENABLED / CONTENT_AUTO_PUBLISH."
        ),
    )
    parser.add_argument(
        "--count", type=int, default=1, help="How many videos to produce."
    )
    parser.add_argument("--title", default=None, help="Force a specific title.")
    parser.add_argument(
        "--no-publish", action="store_true", help="Render only; never upload."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would happen; do nothing."
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    args = build_parser().parse_args()
    runner = VpsRunner(dry_run=args.dry_run, no_publish=args.no_publish)
    published = runner.run(count=args.count, forced_title=args.title)
    sys.exit(0 if published >= 0 else 1)


if __name__ == "__main__":
    main()
