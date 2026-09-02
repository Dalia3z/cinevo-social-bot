"""
platforms
=========
Platform integration modules for the Cinevo AI Social Media Engagement Bot.

Each module exposes a class with a common interface:

    class PlatformHandler:
        name: str
        def fetch_new_items(self) -> list[dict]: ...
        def post_reply(self, item: dict, reply: str) -> bool: ...

The main worker loop discovers enabled handlers and drives them uniformly.
"""

from .youtube import YouTubeHandler
from .meta import MetaHandler
from .tiktok import TikTokHandler
from .quora import QuoraHandler

__all__ = [
    "YouTubeHandler",
    "MetaHandler",
    "TikTokHandler",
    "QuoraHandler",
]
