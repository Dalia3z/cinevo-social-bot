"""
main.py
=======
Entry point for the Cinevo AI Social Media Engagement Bot.

Runs a background worker loop that:
    1. Discovers which platform handlers are enabled (from config).
    2. Polls each enabled platform for new, unreplied comments.
    3. Generates a reply via DeepSeek (ai_handler).
    4. Posts the reply and records it in SQLite (database).
    5. Sleeps a RANDOM delay (3-7 min by default) between replies to avoid
       spam-detection / rate-limit bans.

Run with:
    python main.py            # foreground (useful for testing)
    python main.py --once     # process a single cycle then exit
    python main.py --dry-run  # generate replies but do NOT post anything

For production, run under PM2 (see ecosystem.config.js) so it restarts
automatically and stays alive on the VPS.
"""

import argparse
import logging
import logging.handlers
import os
import random
import signal
import sys
import time
from typing import Dict, List, Optional, Type

from config import settings
from database import db

# --------------------------------------------------------------------------- #
# Logging setup
# --------------------------------------------------------------------------- #
def _setup_logging() -> None:
    """Configure console + rotating file logging."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    log_dir = os.path.dirname(settings.log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        settings.log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


logger = logging.getLogger("main")


# --------------------------------------------------------------------------- #
# Handler registry
# --------------------------------------------------------------------------- #
def _build_handlers() -> Dict[str, object]:
    """Instantiate handlers for the platforms listed in ACTIVE_PLATFORMS."""
    from platforms.meta import MetaHandler
    from platforms.quora import QuoraHandler
    from platforms.tiktok import TikTokHandler
    from platforms.youtube import YouTubeHandler

    registry: Dict[str, Type] = {
        "youtube": YouTubeHandler,
        "meta": MetaHandler,
        "tiktok": TikTokHandler,
        "quora": QuoraHandler,
    }

    handlers: Dict[str, object] = {}
    for name in settings.active_platforms:
        cls = registry.get(name)
        if cls is None:
            logger.warning("Unknown platform '%s' in ACTIVE_PLATFORMS; ignoring.", name)
            continue
        handlers[name] = cls()
        logger.info("Enabled platform handler: %s", name)
    return handlers


# --------------------------------------------------------------------------- #
# Anti-ban random delay
# --------------------------------------------------------------------------- #
def _anti_ban_delay(deadline: Optional[float] = None) -> None:
    """
    Sleep a random duration within the configured min/max range.

    If `deadline` (an absolute time.time() value) is given, the sleep is
    capped so we never overshoot the runtime budget. This lets the bot exit
    cleanly instead of being killed by GitHub Actions' `timeout-minutes`.
    """
    delay = random.uniform(settings.min_delay_seconds, settings.max_delay_seconds)
    if deadline is not None:
        remaining = deadline - time.time()
        if remaining <= 0:
            logger.info("Runtime budget exhausted; skipping anti-ban delay.")
            return
        # Never sleep past the deadline. Leave a small safety margin, but
        # never shrink a positive sleep to zero (that would defeat the
        # anti-ban delay entirely when the budget is nearly spent).
        delay = min(delay, max(1.0, remaining - 5))
    logger.info(
        "Anti-ban delay: sleeping %.0f seconds before next reply...", delay
    )
    time.sleep(delay)


# --------------------------------------------------------------------------- #
# Core worker
# --------------------------------------------------------------------------- #
class EngagementBot:
    """Drives the whole engagement loop."""

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.handlers = _build_handlers()
        self._stop = False

    def _install_signal_handlers(self) -> None:
        """Allow graceful shutdown on SIGINT/SIGTERM (used by PM2/systemd)."""
        def _handle(signum, frame):  # noqa: ARG001
            logger.info("Received signal %s; shutting down gracefully...", signum)
            self._stop = True

        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)

    def run_once(self, max_runtime_seconds: Optional[int] = None) -> int:
        """
        Process one full cycle across all enabled platforms.
        Returns the number of replies posted.

        `max_runtime_seconds` is a hard budget: once exceeded, the bot stops
        starting new replies and returns, so the process exits CLEANLY before
        an external timeout (e.g. GitHub Actions' `timeout-minutes`) kills it.
        """
        deadline: Optional[float] = None
        if max_runtime_seconds and max_runtime_seconds > 0:
            deadline = time.time() + max_runtime_seconds
            logger.info(
                "Runtime budget: %ds (will stop starting new replies after that).",
                max_runtime_seconds,
            )

        total = 0
        for name, handler in self.handlers.items():
            try:
                total += self._process_platform(name, handler, deadline)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unexpected error in platform '%s': %s", name, exc)
        return total

    def _process_platform(
        self, name: str, handler, deadline: Optional[float] = None
    ) -> int:
        """Fetch and reply to new items for a single platform."""
        posted = 0
        try:
            items = handler.fetch_new_items()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch items from '%s': %s", name, exc)
            return 0

        if not items:
            logger.info("[%s] No new comments to handle.", name)
            return 0

        logger.info("[%s] Found %d new item(s) to handle.", name, len(items))
        for item in items:
            if self._stop:
                break
            # Stop starting new replies once the runtime budget is spent.
            if deadline is not None and time.time() >= deadline:
                logger.info(
                    "[%s] Runtime budget reached; stopping after %d reply(ies). "
                    "Remaining items will be handled in the next cycle.",
                    name,
                    posted,
                )
                break
            try:
                if self.dry_run:
                    # In dry-run we still generate but never post.
                    from ai_handler import ai_handler

                    reply = ai_handler.generate_reply(
                        item.get("text", ""), name.capitalize()
                    )
                    logger.info(
                        "[%s][DRY-RUN] Would reply to %s:\n%s",
                        name,
                        item.get("item_id"),
                        reply,
                    )
                    # Mark as handled so dry-run doesn't loop forever.
                    db.mark_replied(name, item["item_id"], item.get("parent_id"))
                    posted += 1
                else:
                    ok = handler.process_comment(item)
                    if ok:
                        posted += 1
                        # Random delay between real posts to avoid bans.
                        # Capped by the runtime budget so we exit cleanly.
                        _anti_ban_delay(deadline)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Failed to process item %s on '%s': %s",
                    item.get("item_id"),
                    name,
                    exc,
                )
        return posted

    def run_forever(self) -> None:
        """Run the polling loop indefinitely until stopped."""
        logger.info("Starting Cinevo Engagement Bot (poll every %ds).",
                    settings.poll_interval_seconds)
        logger.info("Dry-run mode: %s", self.dry_run)
        while not self._stop:
            try:
                posted = self.run_once()
                logger.info("Cycle complete. Replies posted this cycle: %d", posted)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Cycle failed: %s", exc)
            # Wait for the next poll interval (check stop flag periodically).
            waited = 0
            while waited < settings.poll_interval_seconds and not self._stop:
                time.sleep(1)
                waited += 1
        logger.info("Bot stopped.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cinevo AI Social Media Engagement Bot"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit (useful for cron/testing).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate replies but do not post anything.",
    )
    args = parser.parse_args()

    _setup_logging()

    problems = settings.validate()
    if problems:
        for p in problems:
            logger.error("Config problem: %s", p)
        logger.error(
            "Fix the configuration (see .env.example) before running. Aborting."
        )
        sys.exit(1)

    if not settings.bot_enabled:
        logger.info("BOT_ENABLED=false; exiting.")
        sys.exit(0)

    bot = EngagementBot(dry_run=args.dry_run)
    if not bot.handlers:
        logger.error("No platform handlers enabled. Check ACTIVE_PLATFORMS.")
        sys.exit(1)

    # ------------------------------------------------------------------ #
    # Startup diagnostics: make it obvious WHY DeepSeek may never be
    # called. If there are no targets, the bot has nothing to reply to and
    # the DeepSeek API usage will legitimately stay at 0.
    # ------------------------------------------------------------------ #
    logger.info("Active platforms: %s", ", ".join(bot.handlers.keys()))
    for name, handler in bot.handlers.items():
        video_ids = getattr(handler, "video_ids", None)
        channel_ids = getattr(handler, "channel_ids", None)
        if video_ids is not None or channel_ids is not None:
            logger.info(
                "[%s] Targets loaded -> videos=%d channels=%d",
                name,
                len(video_ids or []),
                len(channel_ids or []),
            )
            if not (video_ids or channel_ids):
                logger.warning(
                    "[%s] NO targets configured. The bot will find no comments "
                    "and DeepSeek will NOT be called. Populate targets.json by "
                    "running: python discover_targets.py --all --max 10000",
                    name,
                )

    if args.once:
        posted = bot.run_once(max_runtime_seconds=settings.max_runtime_seconds)
        logger.info("Single cycle finished. Replies posted: %d", posted)
        sys.exit(0)

    bot._install_signal_handlers()
    bot.run_forever()


if __name__ == "__main__":
    main()
