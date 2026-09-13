"""
content_cli.py
==============
Command-line tool for the TikTok / YouTube Shorts content system.

This is the ONLY entry point for content generation. It is deliberately
SEPARATE from `main.py` (the engagement bot), so running it can never
affect the YouTube comment-reply loop.

USAGE
-----
    # Generate a full package for one movie
    python content_cli.py generate "Inception"

    # Generate for several movies at once
    python content_cli.py generate "Inception" "Parasite" "Dark"

    # Build today's plan and generate everything in it
    python content_cli.py plan --days 1 --generate

    # Build a 7-day calendar (no generation)
    python content_cli.py plan --days 7

    # List queued content
    python content_cli.py list
    python content_cli.py list --status draft

    # Show one package as a shooting script
    python content_cli.py show 3

    # Export ready-to-post captions to a text file
    python content_cli.py export --status ready --out captions.txt

    # Mark a package as ready / posted / skipped
    python content_cli.py mark 3 ready
    python content_cli.py mark 3 posted --platform tiktok --url https://...

    # Queue statistics
    python content_cli.py stats

NOTHING IS EVER AUTO-POSTED. This tool only generates and organises text.
"""

import argparse
import io
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import List

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

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_generate(args) -> int:
    """Generate content packages for the given titles."""
    titles: List[str] = list(args.titles or [])
    if not titles:
        logger.error("No titles given. Example: generate \"Inception\"")
        return 1

    created = 0
    for title in titles:
        pkg = content_generator.generate(title, language=args.language)
        if not pkg:
            print(f"[FAIL] {title}")
            continue
        cid = publish_queue.add(pkg, status="draft")
        if cid:
            created += 1
            print(f"[OK]   {title}  ->  queued as id={cid}")
            print()
            print(content_generator.render_teleprompter(pkg))
        else:
            print(f"[FAIL] {title} (could not queue)")

    print(f"\nGenerated {created}/{len(titles)} package(s).")
    return 0 if created else 1


def cmd_plan(args) -> int:
    """Build a content plan (optionally generating scripts for it)."""
    days = max(1, args.days)
    already = publish_queue.titles()

    all_entries = []
    for d in range(days):
        day = datetime.now(timezone.utc) + timedelta(days=d)
        plan = content_planner.build_daily_plan(
            date=day, exclude_titles=already
        )
        all_entries.extend(plan)
        # Avoid picking the same title twice across the multi-day plan.
        already.extend(p["title"] for p in plan)

    if days == 1:
        print(content_planner.render_plan(all_entries))
    else:
        print(content_planner.render_calendar(all_entries, days=days))

    if args.generate:
        print("\n--- Generating scripts ---")
        ok = 0
        for entry in all_entries:
            pkg = content_generator.generate(
                entry["title"],
                language=entry.get("language"),
                extra_context=f"Content angle: {entry['angle_label']}. {entry['brief']}",
            )
            if pkg and publish_queue.add(pkg, status="draft"):
                ok += 1
                print(f"[OK]   {entry['title']} ({entry['angle_label']})")
            else:
                print(f"[FAIL] {entry['title']}")
        print(f"\nGenerated {ok}/{len(all_entries)} script(s).")
    return 0


def cmd_list(args) -> int:
    """List queued content."""
    rows = publish_queue.list(status=args.status, limit=args.limit)
    if not rows:
        print("(queue is empty)")
        return 0
    print(f"{'ID':>4}  {'STATUS':<8}  {'LANG':<6}  TITLE")
    print("-" * 60)
    for r in rows:
        print(f"{r['id']:>4}  {r['status']:<8}  {r['language']:<6}  {r['title']}")
    print(f"\n{len(rows)} item(s).")
    return 0


def cmd_show(args) -> int:
    """Show one package as a shooting script."""
    rows = [r for r in publish_queue.list(limit=1000) if r["id"] == args.id]
    if not rows:
        print(f"No content with id={args.id}.")
        return 1
    print(content_generator.render_teleprompter(rows[0]))
    return 0


def cmd_export(args) -> int:
    """Export captions + hashtags for manual posting."""
    rows = publish_queue.list(status=args.status, limit=args.limit)
    if not rows:
        print("(nothing to export)")
        return 0

    blocks = []
    for r in rows:
        blocks.append(
            f"### {r['title']}  (id={r['id']}, {r['language']})\n"
            f"{content_generator.render_caption_block(r)}\n"
        )
    text = "\n".join(blocks)

    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(f"Exported {len(rows)} caption(s) to {args.out}")
        except OSError as exc:
            print(f"Failed to write {args.out}: {exc}")
            return 1
    else:
        print(text)
    return 0


def cmd_mark(args) -> int:
    """Update the status of a queued item."""
    ok = publish_queue.set_status(
        args.id,
        args.status,
        platform=args.platform or "",
        posted_url=args.url or "",
    )
    if ok:
        print(f"id={args.id} -> {args.status}")
        return 0
    print(f"Failed to update id={args.id}.")
    return 1


def cmd_stats(args) -> int:
    """Print queue statistics."""
    stats = publish_queue.stats()
    if not stats:
        print("(queue is empty)")
        return 0
    print("Content queue:")
    for status in ("draft", "ready", "posted", "skipped"):
        print(f"  {status:<8} {stats.get(status, 0)}")
    total = sum(stats.values())
    print(f"  {'TOTAL':<8} {total}")
    print(f"\nPublished today: {publish_queue.published_today()}"
          f" / {settings.content_daily_publish_limit}")
    return 0


def cmd_video(args) -> int:
    """Render a queued package into an MP4 short (FFmpeg)."""
    from video_maker import video_maker

    row = publish_queue.get(args.id)
    if not row:
        logger.error("No content with id=%s.", args.id)
        return 1

    if not video_maker.available():
        logger.error(
            "FFmpeg (and/or a usable font) is not available. "
            "Install FFmpeg to render videos."
        )
        return 1

    out = video_maker.build(row, out_path=args.out or None)
    if not out:
        logger.error("Video render failed for id=%s.", args.id)
        return 1

    print(f"Video written: {out}")
    if args.mark_ready:
        publish_queue.set_status(args.id, "ready")
        print(f"Marked id={args.id} as ready.")
    return 0


def cmd_publish(args) -> int:
    """Publish a rendered video to YouTube (respects all safety gates)."""
    from youtube_uploader import youtube_uploader

    row = publish_queue.get(args.id)
    if not row:
        logger.error("No content with id=%s.", args.id)
        return 1

    if not args.video:
        logger.error("--video PATH is required (the rendered MP4).")
        return 1

    if not youtube_uploader.can_publish():
        logger.error(
            "Publishing is blocked by a safety gate. Check CONTENT_ENABLED, "
            "CONTENT_AUTO_PUBLISH, OAuth credentials and the daily limit."
        )
        return 1

    url = youtube_uploader.publish(args.video, row, queue_id=args.id)
    if not url:
        logger.error("Publish failed for id=%s.", args.id)
        return 1
    print(f"Published: {url}")
    return 0


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="content_cli",
        description=(
            "TikTok / YouTube Shorts content generator for Cinevo. "
            "Generates scripts, captions and hashtags. NEVER auto-posts."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # generate
    p_gen = sub.add_parser("generate", help="Generate packages for titles.")
    p_gen.add_argument("titles", nargs="+", help="Movie/series titles.")
    p_gen.add_argument("--language", default=None, help="Target language.")
    p_gen.set_defaults(func=cmd_generate)

    # plan
    p_plan = sub.add_parser("plan", help="Build a content plan/calendar.")
    p_plan.add_argument("--days", type=int, default=1, help="Days to plan.")
    p_plan.add_argument(
        "--generate", action="store_true", help="Also generate the scripts."
    )
    p_plan.set_defaults(func=cmd_plan)

    # list
    p_list = sub.add_parser("list", help="List queued content.")
    p_list.add_argument("--status", default="", help="Filter by status.")
    p_list.add_argument("--limit", type=int, default=50, help="Max rows.")
    p_list.set_defaults(func=cmd_list)

    # show
    p_show = sub.add_parser("show", help="Show one package.")
    p_show.add_argument("id", type=int, help="Content id.")
    p_show.set_defaults(func=cmd_show)

    # export
    p_exp = sub.add_parser("export", help="Export captions for posting.")
    p_exp.add_argument("--status", default="ready", help="Status to export.")
    p_exp.add_argument("--limit", type=int, default=100, help="Max rows.")
    p_exp.add_argument("--out", default="", help="Output file (default stdout).")
    p_exp.set_defaults(func=cmd_export)

    # mark
    p_mark = sub.add_parser("mark", help="Change an item's status.")
    p_mark.add_argument("id", type=int, help="Content id.")
    p_mark.add_argument(
        "status", choices=["draft", "ready", "posted", "skipped"], help="New status."
    )
    p_mark.add_argument("--platform", default="", help="tiktok / youtube / ...")
    p_mark.add_argument("--url", default="", help="Posted URL.")
    p_mark.set_defaults(func=cmd_mark)

    # stats
    p_stats = sub.add_parser("stats", help="Queue statistics.")
    p_stats.set_defaults(func=cmd_stats)

    # video
    p_vid = sub.add_parser("video", help="Render a queued package to MP4.")
    p_vid.add_argument("id", type=int, help="Content id.")
    p_vid.add_argument("--out", default="", help="Output MP4 path.")
    p_vid.add_argument(
        "--mark-ready", action="store_true", help="Mark as ready after render."
    )
    p_vid.set_defaults(func=cmd_video)

    # publish
    p_pub = sub.add_parser("publish", help="Publish a rendered video to YouTube.")
    p_pub.add_argument("id", type=int, help="Content id.")
    p_pub.add_argument("--video", required=True, help="Path to the rendered MP4.")
    p_pub.set_defaults(func=cmd_publish)

    return parser


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
