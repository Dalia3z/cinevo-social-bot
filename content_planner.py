"""
content_planner.py
==================
Builds a DAILY short-video content calendar for TikTok / YouTube Shorts.

WHAT THIS DOES
--------------
1. Picks movie/series titles to feature (from a curated pool + optional
   trending sources).
2. Assigns each pick a "content angle" (e.g. hidden gem, plot twist, best
   scene) so videos don't all look the same.
3. Produces an ordered plan for the day (posting times + titles + angles).
4. Can hand the plan to `content_generator` to produce full scripts.

ISOLATION
---------
Reads `settings` only. Never touches the YouTube reply bot, its database,
or its loop. Safe to run alongside the engagement bot.

NO AUTO-POSTING
---------------
This module only PLANS and GENERATES text. Publishing is manual by design
(TikTok/Shorts have no safe public posting API for this use case).
"""

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Content angles - rotating these keeps the feed varied (avoids repetition).
# --------------------------------------------------------------------------- #
ANGLES: List[Dict[str, str]] = [
    {
        "key": "hidden_gem",
        "label": "Hidden Gem",
        "brief": "Frame it as an underrated movie most people missed.",
    },
    {
        "key": "plot_twist",
        "label": "Insane Plot Twist",
        "brief": "Tease the twist WITHOUT spoiling it. Build suspense.",
    },
    {
        "key": "best_scene",
        "label": "Best Scene",
        "brief": "Describe one unforgettable scene and why it hits hard.",
    },
    {
        "key": "ending_explained",
        "label": "Ending Explained",
        "brief": "Offer a fresh interpretation of the ending.",
    },
    {
        "key": "if_you_liked",
        "label": "If You Liked X",
        "brief": "Recommend it to fans of a similar, more famous movie.",
    },
    {
        "key": "true_story",
        "label": "Based On A True Story",
        "brief": "Highlight the real events behind the film.",
    },
    {
        "key": "one_take",
        "label": "One-Take Wonder",
        "brief": "Focus on a technical/cinematic achievement.",
    },
    {
        "key": "watch_tonight",
        "label": "Watch Tonight",
        "brief": "Quick, punchy recommendation for tonight's watch.",
    },
]

# --------------------------------------------------------------------------- #
# Curated title pool. Kept broad and evergreen (no licensing issues with
# titles themselves). Extend freely - or feed your own list via the CLI.
# --------------------------------------------------------------------------- #
DEFAULT_TITLE_POOL: List[str] = [
    # Sci-fi / mind-benders
    "Inception", "Interstellar", "The Matrix", "Arrival", "Blade Runner 2049",
    "Shutter Island", "Memento", "Predestination", "Coherence", "Primer",
    "The Prestige", "Tenet", "Ex Machina", "Moon", "Annihilation",
    # Thrillers / crime
    "Se7en", "Gone Girl", "Prisoners", "Nightcrawler", "Zodiac",
    "The Silence of the Lambs", "Oldboy", "Parasite", "Memories of Murder",
    "No Country for Old Men", "Sicario", "Wind River", "Mystic River",
    # Drama / classics
    "The Godfather", "Fight Club", "The Shawshank Redemption", "Forrest Gump",
    "Good Will Hunting", "The Green Mile", "Whiplash", "The Truman Show",
    "American History X", "Requiem for a Dream", "Trainspotting",
    # Action / adventure
    "Mad Max: Fury Road", "John Wick", "The Dark Knight", "Gladiator",
    "Heat", "The Raid", "Dredd", "Edge of Tomorrow", "Baby Driver",
    # Horror
    "Get Out", "Hereditary", "The Witch", "It Follows", "The Babadook",
    "Sinister", "The Conjuring", "A Quiet Place", "Midsommar", "Talk to Me",
    # Animation / family
    "Spirited Away", "Your Name", "Grave of the Fireflies", "Coco",
    "Up", "WALL-E", "Inside Out", "The Lion King", "Toy Story",
    # International
    "City of God", "Amelie", "Cinema Paradiso", "The Lives of Others",
    "Pan's Labyrinth", "Rashomon", "Seven Samurai", "The Intouchables",
    # Series
    "Breaking Bad", "Dark", "Severance", "True Detective", "Chernobyl",
    "The Bear", "Succession", "Better Call Saul", "Mindhunter", "Fargo",
]

# Default posting slots (local-ish, 24h). TikTok peaks: early morning,
# lunch, and especially evening.
DEFAULT_SLOTS: List[str] = ["09:00", "13:00", "18:00", "21:00"]


class ContentPlanner:
    """Builds a daily short-video plan."""

    def __init__(self) -> None:
        self.title_pool = list(DEFAULT_TITLE_POOL)
        self.slots = list(DEFAULT_SLOTS)
        self.posts_per_day = settings.content_posts_per_day
        self.language = settings.content_language

    # ------------------------------------------------------------------ #
    # Planning
    # ------------------------------------------------------------------ #
    def build_daily_plan(
        self,
        date: Optional[datetime] = None,
        exclude_titles: Optional[List[str]] = None,
        titles: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Return an ordered list of plan entries for one day.

        Each entry: {slot, title, angle_key, angle_label, brief, language}
        """
        date = date or datetime.now(timezone.utc)
        exclude = {t.lower() for t in (exclude_titles or [])}

        pool = [t for t in (titles or self.title_pool) if t.lower() not in exclude]
        if not pool:
            logger.warning("Title pool is empty after exclusions; reusing full pool.")
            pool = list(titles or self.title_pool)

        random.shuffle(pool)
        n = max(1, min(self.posts_per_day, len(pool)))
        chosen = pool[:n]

        # Rotate angles so consecutive videos differ.
        angles = list(ANGLES)
        random.shuffle(angles)

        slots = self._slots_for(n)
        plan: List[Dict] = []
        for i, title in enumerate(chosen):
            angle = angles[i % len(angles)]
            plan.append(
                {
                    "slot": slots[i],
                    "title": title,
                    "angle_key": angle["key"],
                    "angle_label": angle["label"],
                    "brief": angle["brief"],
                    "language": self.language,
                    "date": date.strftime("%Y-%m-%d"),
                }
            )
        logger.info("Built a %d-post plan for %s.", len(plan), date.strftime("%Y-%m-%d"))
        return plan

    def _slots_for(self, n: int) -> List[str]:
        """Return n posting slots, spreading across the configured times."""
        if n <= len(self.slots):
            # Evenly sample the configured slots.
            step = len(self.slots) / n
            return [self.slots[int(i * step)] for i in range(n)]
        # More posts than slots: add hourly slots after the last one.
        out = list(self.slots)
        last = datetime.strptime(self.slots[-1], "%H:%M")
        while len(out) < n:
            last = last + timedelta(hours=1)
            out.append(last.strftime("%H:%M"))
        return out[:n]

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    @staticmethod
    def render_plan(plan: List[Dict]) -> str:
        """Human-readable daily plan."""
        if not plan:
            return "(empty plan)"
        lines = [
            "=" * 60,
            f"  CONTENT PLAN - {plan[0].get('date', '')}",
            "=" * 60,
        ]
        for p in plan:
            lines += [
                "",
                f"[{p['slot']}]  {p['title']}",
                f"    Angle : {p['angle_label']}",
                f"    Brief : {p['brief']}",
                f"    Lang  : {p['language']}",
            ]
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def render_calendar(plan: List[Dict], days: int = 7) -> str:
        """Compact multi-day calendar view."""
        lines = ["=" * 60, f"  {days}-DAY CONTENT CALENDAR", "=" * 60]
        by_date: Dict[str, List[Dict]] = {}
        for p in plan:
            by_date.setdefault(p.get("date", ""), []).append(p)
        for d in sorted(by_date):
            lines.append("")
            lines.append(f"--- {d} ---")
            for p in by_date[d]:
                lines.append(f"  {p['slot']}  {p['title']}  ({p['angle_label']})")
        lines.append("")
        return "\n".join(lines)


# Module-level singleton.
content_planner = ContentPlanner()
