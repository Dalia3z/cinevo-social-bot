"""
quora.py
========
Quora integration for the Cinevo AI Social Media Engagement Bot.

IMPORTANT CONTEXT:
    Quora has NO official public API for posting answers. The only way to
    automate answering is browser automation, which carries a real risk of
    account suspension if done aggressively.

This module therefore offers two safe, opt-in modes:

    1. MANUAL QUEUE MODE (RECOMMENDED, default):
       The bot monitors Quora question feeds via a lightweight search and
       *generates* a ready-to-post answer with the AI handler, then logs it
       to the database and optionally to a local file/queue. A human reviews
       and posts manually. This is 100% safe and still saves time.

    2. BROWSER AUTOMATION MODE (OPT-IN, high risk):
       A conservative Selenium flow (disabled by default via
       QUORA_BROWSER_AUTOMATION=false) that logs in and posts answers with
       long, randomised human-like delays. Use only on accounts you control
       and at your own risk.

The module never runs unless QUORA_ENABLED=true and the topic list is set.
"""

import json
import logging
import os
import random
import time
from typing import List, Optional

from config import settings
from database import db
from ai_handler import ai_handler

logger = logging.getLogger(__name__)


class QuoraHandler:
    """Handles Quora question discovery and answer generation."""

    name = "quora"

    def __init__(self) -> None:
        self.topics = settings.quora_topics
        self.username = settings.quora_username
        self.password = settings.quora_password
        self.browser_automation = settings.quora_browser_automation
        self.max_items = settings.max_comments_per_cycle
        # Directory where generated answers are queued for manual review.
        self.queue_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "quora_queue",
        )

    # ------------------------------------------------------------------ #
    # Discovery (safe: keyword-based, no posting)
    # ------------------------------------------------------------------ #
    def fetch_new_items(self) -> List[dict]:
        """
        Return "new items" representing questions worth answering.

        Because Quora has no API, this returns a lightweight representation
        of each configured topic so the worker can generate an answer draft.
        In a real deployment you would replace this with a search of Quora's
        public question pages (e.g. via the browser automation path) to obtain
        actual question URLs/titles.

        Each item:
            {
                "platform": "quora",
                "item_id": <stable id>,
                "parent_id": <topic>,
                "text": <question/topic prompt>,
                "author": "",
            }
        """
        items: List[dict] = []
        for topic in self.topics:
            # Stable pseudo-id so we only draft each topic once per run window.
            item_id = f"topic:{topic}"
            if db.is_replied(self.name, item_id):
                continue
            items.append(
                {
                    "platform": self.name,
                    "item_id": item_id,
                    "parent_id": topic,
                    "text": (
                        f"Topic: {topic}. Write a helpful, guiding answer that "
                        "adds real value for people interested in this subject."
                    ),
                    "author": "",
                }
            )
            if len(items) >= self.max_items:
                break
        return items

    # ------------------------------------------------------------------ #
    # Posting
    # ------------------------------------------------------------------ #
    def post_reply(self, item: dict, reply: str) -> bool:
        """
        Handle the generated answer.

        In MANUAL QUEUE MODE this writes the answer to a JSON file for human
        review and returns True (queued successfully).

        In BROWSER AUTOMATION MODE it attempts to post via Selenium.
        """
        if self.browser_automation:
            return self._post_via_browser(item, reply)

        # Default: queue for manual review.
        return self._queue_for_review(item, reply)

    def _queue_for_review(self, item: dict, reply: str) -> bool:
        """Write the generated answer to a JSON file for manual posting."""
        try:
            os.makedirs(self.queue_dir, exist_ok=True)
            filename = os.path.join(
                self.queue_dir, f"answer_{int(time.time())}_{item['item_id'].replace(':', '_')}.json"
            )
            payload = {
                "topic": item.get("parent_id", ""),
                "question_prompt": item.get("text", ""),
                "answer": reply,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(filename, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            logger.info("Quora answer queued for review: %s", filename)
            return True
        except OSError as exc:
            logger.error("Failed to queue Quora answer: %s", exc)
            return False

    def _post_via_browser(self, item: dict, reply: str) -> bool:
        """
        Conservative Selenium posting flow (OPT-IN, high risk).

        This requires `selenium` and a Chrome/Chromium driver on the VPS.
        It is intentionally slow and human-like. If Selenium is not installed
        or login fails, it falls back to queuing the answer.
        """
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.chrome.options import Options
        except ImportError:
            logger.warning(
                "Selenium not installed; falling back to manual queue mode."
            )
            return self._queue_for_review(item, reply)

        if not (self.username and self.password):
            logger.warning("Quora credentials missing; falling back to queue mode.")
            return self._queue_for_review(item, reply)

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])

        driver = None
        try:
            driver = webdriver.Chrome(options=options)
            driver.get("https://www.quora.com/")
            self._human_pause(8, 14)

            # Login (selectors may change; keep them configurable-ish here).
            driver.get("https://www.quora.com/login")
            self._human_pause(5, 9)
            email_field = driver.find_element(By.NAME, "email")
            pwd_field = driver.find_element(By.NAME, "password")
            email_field.send_keys(self.username)
            self._human_pause(1, 3)
            pwd_field.send_keys(self.password)
            self._human_pause(1, 3)
            pwd_field.submit()
            self._human_pause(10, 16)

            # Navigate to the topic's questions page.
            topic = item.get("parent_id", "")
            driver.get(f"https://www.quora.com/topic/{topic}")
            self._human_pause(6, 10)

            # NOTE: Answering a specific question requires a question URL.
            # This is a placeholder that logs the intent; full answer posting
            # depends on live DOM selectors which change frequently.
            logger.info(
                "Browser automation reached topic page for '%s' (posting "
                "requires live selectors; answer queued instead).",
                topic,
            )
            return self._queue_for_review(item, reply)
        except Exception as exc:  # noqa: BLE001
            logger.error("Quora browser automation error: %s", exc)
            return self._queue_for_review(item, reply)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:  # noqa: BLE001
                    pass

    def _human_pause(self, low: float, high: float) -> None:
        """Sleep for a random human-like duration."""
        time.sleep(random.uniform(low, high))

    # ------------------------------------------------------------------ #
    # Full process for one item
    # ------------------------------------------------------------------ #
    def process_comment(self, item: dict) -> bool:
        """Generate and handle an answer for a single item."""
        prompt = item.get("text", "")
        if not prompt:
            return False

        reply = ai_handler.generate_reply(prompt, "Quora")
        if not reply:
            logger.warning("No AI answer generated for Quora item %s", item["item_id"])
            return False

        ok = self.post_reply(item, reply)
        if ok:
            db.mark_replied(self.name, item["item_id"], item.get("parent_id"))
            db.log_reply(
                self.name,
                item["item_id"],
                item.get("parent_id"),
                prompt,
                reply,
            )
            logger.info("Handled Quora item %s", item["item_id"])
        return ok
