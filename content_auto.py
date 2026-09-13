"""
content_auto.py
===============
End-to-end automation for the short-form content system:

    plan  ->  generate (DeepSeek)  ->  render video (FFmpeg)  ->  publish (YouTube)

This is the entry point used by the GitHub Actions workflow
(`.github/workflows/content-auto.yml`). It is COMPLETELY SEPARATE from the
comment-reply bot (`main.py`) and never touches the reply database.

SAFETY
------
Every stage is gated:
  * CONTENT_ENABLED=false        -> the whole run exits immediately.
  * CONTENT_AUTO_PUBLISH=false   -> videos are rendered but NOT uploaded.
  * CONTENT_DAILY_PUBLISH_LIMIT  -> hard cap on uploads per day.
  * CONTENT_PRIVACY              -> defaults to `unlisted` for review.

USAGE
-----
    # Full auto run (respects all gates)
    python content_auto.py

    # Render only, never publish (safe preview)
    python content_auto.py --no-publish

    # Dry run: show what WOULD happen, do nothing
    python content_auto.py --dry-run

    # Force a specific title
    python content_auto.py --title "Inception"
"""

import argparse
import io
import logging
import sys
from typing import Dict, List, Optional

# Make stdout UTF-8 safe on Windows (cp1252 would crash on emoji/Arabic).
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from config import settings
from content_generator import content_generator
from content_planner import content_planner
from publish_queue import publish_queue
from video_maker import video_maker
from youtube_uploader import youtube_uploader

logger = logging.getLogger(__name__)


class ContentAutoRunner:
    """Runs the full content pipeline with all safety gates."""

    def __init__(self, dry_run: bool = False, no_publish: bool = False) -> None:
        self.dry_run = dry_run
        self.no_publish = no_publish

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _pick_titles(self, count: int, forced: Optional[str]) -> List[str]:
        """Choose which titles to produce today."""
        if forced:
            return [forced]

        # Build today's plan, skipping titles already in the queue.
        plan = content_planner.build_daily_plan(
            exclude_titles=publish_queue.titles()
        )
        titles = [entry["title"] for entry in plan][:count]
        if not titles:
            # Everything is already queued; fall back to the raw pool.
            titles = list(content_planner.title_pool)[:count]
        return titles

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #
    def run(self, count: int = 1, forced_title: Optional[str] = None) -> int:
        """Run the pipeline. Returns the number of videos published."""
        if not settings.content_enabled:
            logger.warning(
                "CONTENT_ENABLED is false; the content subsystem is disabled. "
                "Set CONTENT_ENABLED=true to enable it."
            )
            return 0

        titles = self._pick_titles(count, forced_title)
        if not titles:
            logger.warning("No titles available to produce.")
            return 0

        logger.info("Content run: %d title(s) -> %s", len(titles), ", ".join(titles))

        if self.dry_run:
            for t in titles:
                print(f"[DRY-RUN] would generate + render + publish: {t}")
            print(
                f"[DRY-RUN] auto_publish={settings.content_auto_publish} "
                f"privacy={settings.content_privacy} "
                f"daily_limit={settings.content_daily_publish_limit} "
                f"published_today={publish_queue.published_today()}"
            )
            return 0

        published = 0
        for title in titles:
            pkg = self._make_package(title)
            if not pkg:
                continue

            queue_id = publish_queue.add(pkg, status="draft")
            if queue_id is None:
                continue

            video_path = self._render(pkg)
            if not video_path:
                logger.warning("No video produced for '%s'; leaving as draft.", title)
                continue

            # Mark ready now that a video exists.
            publish_queue.set_status(queue_id, "ready")

            if self.no_publish:
                logger.info(
                    "Rendered '%s' -> %s (publishing disabled by --no-publish).",
                    title,
                    video_path,
                )
                continue

            if self._publish(video_path, pkg, queue_id):
                published += 1

        logger.info("Content run complete: %d video(s) published.", published)
        return published

    def _make_package(self, title: str) -> Optional[Dict]:
        """Generate the text package for `title`."""
        logger.info("Generating content for '%s'...", title)
        pkg = content_generator.generate(title)
        if not pkg:
            logger.error("Generation failed for '%s'.", title)
            return None
        return pkg

    def _render(self, pkg: Dict) -> Optional[str]:
        """Render the video for a package."""
        if not video_maker.available():
            logger.warning(
                "FFmpeg unavailable; skipping video render. "
                "The text package is still queued for manual use."
            )
            return None
        return video_maker.build(pkg)

    def _publish(self, video_path: str, pkg: Dict, queue_id: int) -> bool:
        """Publish the video, respecting every safety gate."""
        if not youtube_uploader.can_publish():
            logger.info(
                "Publishing skipped for '%s' (gate closed). "
                "Video kept at %s.",
                pkg.get("title"),
                video_path,
            )
            return False
        url = youtube_uploader.publish(video_path, pkg, queue_id=queue_id)
        return bool(url)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="content_auto",
        description=(
            "Automated short-video pipeline: generate -> render -> publish. "
            "Respects CONTENT_ENABLED / CONTENT_AUTO_PUBLISH / daily limit."
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
    runner = ContentAutoRunner(dry_run=args.dry_run, no_publish=args.no_publish)
    published = runner.run(count=args.count, forced_title=args.title)
    sys.exit(0 if published >= 0 else 1)


if __name__ == "__main__":
    main()
