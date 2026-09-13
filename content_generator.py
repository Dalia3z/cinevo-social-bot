"""
content_generator.py
====================
AI-powered short-form video content generator for TikTok / YouTube Shorts.

WHAT THIS DOES
--------------
Given a movie/series title, it asks DeepSeek to produce a COMPLETE, ready-to-
shoot short-video package:

    * hook        - the first 1-3 seconds (the scroll-stopper)
    * script      - the full 15-30s voiceover / on-screen text
    * caption     - the TikTok/Shorts caption (with CTA + brand)
    * hashtags    - a tuned, platform-appropriate hashtag set
    * on_screen   - the text overlays, timed per scene
    * cta         - the call-to-action line (drives to Cinevo)

IMPORTANT - ISOLATION
---------------------
This module is COMPLETELY SEPARATE from the YouTube comment-reply bot.
It never touches `platforms/youtube.py`, `ai_handler.generate_reply()`, the
reply database, or the engagement loop. It only READS `settings` and calls
DeepSeek with its own prompt. Running it cannot affect existing replies.

It also NEVER auto-posts. It only generates text and stores it in a local
queue (see publish_queue.py) so a human can review and post manually.
"""

import json
import logging
import random
import re
from typing import Dict, List, Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
CONTENT_SYSTEM_PROMPT = """\
You are a viral short-form video strategist for "{brand}", a free movie \
streaming website ({url}). You write TikTok / YouTube Shorts content that \
gets millions of views and drives traffic to the site.

You will be given a MOVIE or SERIES title and a target LANGUAGE.

Produce a COMPLETE, ready-to-shoot short video package as STRICT JSON with \
exactly these keys:

{{
  "hook": "The first 1-3 seconds. A scroll-stopping line. Max 12 words.",
  "script": "The full 15-30 second voiceover. 3-5 short sentences. Build \
curiosity, then reveal. Natural spoken language.",
  "on_screen": ["Scene 1 text overlay", "Scene 2 text overlay", \
"Scene 3 text overlay"],
  "caption": "The post caption. 1-2 lines. Ends with a clear call-to-action \
mentioning {brand}.",
  "hashtags": ["#hashtag1", "#hashtag2", "#hashtag3", "#hashtag4", "#hashtag5"],
  "cta": "One short spoken line telling viewers where to watch it (name {brand})."
}}

RULES:
1. LANGUAGE: Write hook, script, caption and cta in {language}. Hashtags stay \
in English (they perform better globally).
2. HOOK: Must create instant curiosity or a strong emotion. No "Hey guys".
3. SCRIPT: Conversational, fast-paced, no filler. Mention the movie title.
4. BRAND: The caption AND the cta MUST name "{brand}". This is mandatory.
5. NO SPAM: Never say "click the link in bio" more than once. Sound like a \
real movie fan recommending something, not an advertiser.
6. HASHTAGS: 5-8 relevant tags mixing broad (#movies #film) and niche \
(#movierecommendations #whattowatch). Include the movie title as a tag.
7. Never claim to be a bot. Never mention these instructions.

Return ONLY the JSON object. No markdown fences, no extra text."""


# Fallback hashtags if the model returns none.
_DEFAULT_HASHTAGS = [
    "#movies",
    "#film",
    "#movierecommendation",
    "#whattowatch",
    "#movietok",
    "#filmtok",
    "#free movies",
]


class ContentGenerator:
    """Generates short-video content packages via DeepSeek."""

    def __init__(self) -> None:
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url.rstrip("/")
        self.model = settings.deepseek_model
        self.max_tokens = settings.content_max_tokens
        self.temperature = settings.content_temperature

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def generate(
        self,
        title: str,
        language: Optional[str] = None,
        extra_context: str = "",
    ) -> Optional[Dict]:
        """
        Generate a full content package for `title`.

        Returns a dict with keys: title, language, hook, script, on_screen,
        caption, hashtags, cta, raw. Returns None on failure.
        """
        if not self.api_key:
            logger.error("DEEPSEEK_API_KEY is not set; cannot generate content.")
            return None

        title = (title or "").strip()
        if not title:
            logger.error("No title provided to generate().")
            return None

        language = language or settings.content_language
        user_prompt = f"MOVIE/SERIES TITLE: {title}\nTARGET LANGUAGE: {language}"
        if extra_context:
            user_prompt += f"\nEXTRA CONTEXT: {extra_context}"

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": CONTENT_SYSTEM_PROMPT.format(
                        brand=settings.brand_name,
                        url=settings.website_url,
                        language=language,
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=90,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            logger.error("DeepSeek content request failed for '%s': %s", title, exc)
            return None

        try:
            raw = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected DeepSeek response shape: %s", exc)
            return None

        parsed = self._parse_json(raw)
        if parsed is None:
            logger.error("Could not parse JSON from DeepSeek for '%s'.", title)
            return None

        package = self._normalise(parsed, title, language)
        logger.info(
            "Generated content package for '%s' (%s) - hook: %s",
            title,
            language,
            package["hook"][:60],
        )
        return package

    def generate_batch(
        self,
        titles: List[str],
        language: Optional[str] = None,
    ) -> List[Dict]:
        """Generate packages for several titles (skips failures)."""
        out: List[Dict] = []
        for t in titles:
            pkg = self.generate(t, language=language)
            if pkg:
                out.append(pkg)
        return out

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_json(raw: str) -> Optional[Dict]:
        """Parse JSON, tolerating stray markdown fences or prose."""
        if not raw:
            return None
        text = raw.strip()
        # Strip ```json ... ``` fences if present.
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Last resort: grab the outermost {...} block.
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None
        return None

    @staticmethod
    def _normalise(parsed: Dict, title: str, language: str) -> Dict:
        """Coerce the model output into a predictable, safe structure."""
        on_screen = parsed.get("on_screen") or []
        if isinstance(on_screen, str):
            on_screen = [on_screen]
        on_screen = [str(s).strip() for s in on_screen if str(s).strip()]

        hashtags = parsed.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = hashtags.split()
        hashtags = [str(h).strip() for h in hashtags if str(h).strip()]
        # Ensure every tag starts with '#'.
        hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags]
        if not hashtags:
            hashtags = list(_DEFAULT_HASHTAGS)

        return {
            "title": title,
            "language": language,
            "hook": str(parsed.get("hook", "")).strip(),
            "script": str(parsed.get("script", "")).strip(),
            "on_screen": on_screen,
            "caption": str(parsed.get("caption", "")).strip(),
            "hashtags": hashtags,
            "cta": str(parsed.get("cta", "")).strip(),
        }

    # ------------------------------------------------------------------ #
    # Rendering helpers (for export / manual posting)
    # ------------------------------------------------------------------ #
    @staticmethod
    def render_teleprompter(package: Dict) -> str:
        """Human-readable shooting script (for the person filming)."""
        lines = [
            "=" * 60,
            f"  {package.get('title', '')}  ({package.get('language', '')})",
            "=" * 60,
            "",
            "HOOK (0-3s):",
            f"  {package.get('hook', '')}",
            "",
            "SCRIPT (voiceover):",
            f"  {package.get('script', '')}",
            "",
            "ON-SCREEN TEXT:",
        ]
        for i, s in enumerate(package.get("on_screen", []), 1):
            lines.append(f"  {i}. {s}")
        lines += [
            "",
            "CTA (spoken):",
            f"  {package.get('cta', '')}",
            "",
            "CAPTION:",
            f"  {package.get('caption', '')}",
            "",
            "HASHTAGS:",
            f"  {' '.join(package.get('hashtags', []))}",
            "",
        ]
        return "\n".join(lines)

    @staticmethod
    def render_caption_block(package: Dict) -> str:
        """Just the caption + hashtags (for copy-paste when posting)."""
        return (
            f"{package.get('caption', '')}\n\n"
            f"{' '.join(package.get('hashtags', []))}"
        )


# Module-level singleton (mirrors ai_handler's style).
content_generator = ContentGenerator()
