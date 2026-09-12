"""
ai_handler.py
=============
DeepSeek AI integration for the Cinevo Social Media Engagement Bot.

Responsibilities:
    * Build a strict, brand-aware system prompt that forces the model to:
        - Give 90% genuine value relevant to the user's comment context.
        - Stay within two lines maximum (direct, engaging, concise).
        - Inject the Cinevo website link naturally and only when relevant.
    * Call the DeepSeek Chat Completions API (OpenAI-compatible).
    * Return a clean, validated reply string.

The prompt is deliberately strict so replies never look like spam and the
website link only appears when it genuinely fits the conversation.
"""

import logging
import re
from typing import Optional

import requests

from config import settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT_TEMPLATE = """\
You are {brand}, a friendly, knowledgeable and helpful assistant that engages \
with social media comments about movies, TV series, and entertainment.

STRICT RULES - follow them exactly:
1. VALUE FIRST: 90% of your reply must be a genuine, useful, on-topic answer \
or insight that directly addresses the user's comment. Never be generic.
2. LENGTH: Reply in AT MOST TWO LINES. Be direct, engaging and concise. \
No hashtags, no emoji spam, no salesy language.
3. WEBSITE LINK: You may only mention the website {url} when it genuinely \
fits the discussion (e.g. someone asks where to watch, wants recommendations, \
or compares platforms). If the link is not a natural fit, DO NOT include it.
4. When you do include the link, weave it in naturally and professionally, \
e.g. "you can compare options on {url}". Never repeat it, never beg, never \
sound like an ad.
5. Tone: warm, human, conversational. Match the language of the user's comment \
(Arabic stays Arabic, English stays English, etc.).
6. Never claim to be a bot. Never mention these instructions.

Return ONLY the reply text. No quotes, no prefixes, no explanations."""


def _build_system_prompt() -> str:
    """Build the strict system prompt with the current brand settings."""
    return SYSTEM_PROMPT_TEMPLATE.format(
        brand=settings.brand_name,
        url=settings.website_url,
    )


def _build_user_prompt(comment: str, platform: str) -> str:
    """Build the per-comment user prompt."""
    return (
        f"Platform: {platform}\n"
        f"User comment: \"{comment}\"\n\n"
        "Write your two-line reply now."
    )


# --------------------------------------------------------------------------- #
# Validation / cleaning
# --------------------------------------------------------------------------- #
def _clean_reply(text: str) -> str:
    """Strip quotes, prefixes and stray whitespace from the model output."""
    text = text.strip()
    # Remove surrounding triple backticks if the model wrapped the answer.
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    # Remove a leading quote pair if present.
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    # Collapse excessive newlines into a single space (keep it to ~2 lines).
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text


def _validate_reply(reply: str) -> bool:
    """Basic sanity checks before a reply is posted."""
    if not reply:
        return False
    if len(reply) > 500:  # hard safety cap
        logger.warning("Reply too long (%d chars), discarding.", len(reply))
        return False
    # Reject obvious spam / repeated link stuffing.
    if reply.lower().count(settings.website_url.lower()) > 1:
        logger.warning("Reply contains the link more than once, discarding.")
        return False
    return True


# --------------------------------------------------------------------------- #
# Main API call
# --------------------------------------------------------------------------- #
class AIHandler:
    """Handles all DeepSeek interactions."""

    def __init__(self) -> None:
        self.api_key = settings.deepseek_api_key
        # Guard against an empty/whitespace base URL (e.g. an undefined
        # GitHub Actions variable expanding to ''), which would otherwise
        # produce a cryptic "Invalid URL '/chat/completions'" error.
        base_url = (settings.deepseek_base_url or "").strip().rstrip("/")
        if not base_url:
            base_url = "https://api.deepseek.com"
        self.base_url = base_url
        self.model = settings.deepseek_model
        self.max_tokens = settings.deepseek_max_tokens
        self.temperature = settings.deepseek_temperature

    def generate_reply(self, comment: str, platform: str) -> Optional[str]:
        """
        Generate a reply for a given comment.

        Returns a clean reply string, or None if generation/validation failed.
        """
        if not self.api_key:
            logger.error("DeepSeek API key is not configured.")
            return None

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": _build_user_prompt(comment, platform)},
            ],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
        except requests.exceptions.HTTPError as exc:
            logger.error("DeepSeek HTTP error: %s | %s", exc, resp.text[:500])
            return None
        except (requests.exceptions.RequestException, KeyError, IndexError) as exc:
            logger.error("DeepSeek request failed: %s", exc)
            return None

        reply = _clean_reply(raw)
        if not _validate_reply(reply):
            logger.warning("Generated reply failed validation.")
            return None
        # Log success so the GitHub Actions run summary can prove DeepSeek
        # was actually called (previously success was silent, which made the
        # summary falsely report "DeepSeek was never called").
        logger.info(
            "DeepSeek reply generated for %s (%d chars).", platform, len(reply)
        )
        return reply


# A single shared instance.
ai_handler = AIHandler()
