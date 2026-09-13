"""
video_maker.py
==============
Turns a generated content package (see content_generator.py) into a real
vertical short video (1080x1920, 9:16) using FFmpeg.

WHAT IT PRODUCES
----------------
A "kinetic text" short: a moving gradient background + the hook/script text
animated on screen + an optional AI voiceover (edge-tts) + optional background
music. This is 100% original content (no copyrighted movie footage), so it is
safe to publish on YouTube Shorts / TikTok.

WHY TEXT-BASED (NOT MOVIE CLIPS)
--------------------------------
Using real movie footage triggers Content ID claims and copyright strikes,
which would put the SAME channel that runs the reply bot at risk. Text-based
shorts are fully original and cannot be claimed.

ISOLATION
---------
This module is COMPLETELY SEPARATE from the comment-reply bot. It never touches
platforms/youtube.py, ai_handler.generate_reply(), or the reply database.

REQUIREMENTS
------------
* FFmpeg must be on PATH (GitHub Actions: `apt-get install -y ffmpeg`).
* Optional: `edge-tts` for the voiceover (`pip install edge-tts`).
* Optional: a background music file (CONTENT_MUSIC_PATH).

If FFmpeg is missing, `available()` returns False and callers should skip
video generation gracefully (the text package is still usable manually).
"""

import logging
import os
import shutil
import subprocess
import tempfile
import textwrap
from typing import Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

# Vertical short dimensions (9:16).
WIDTH = 1080
HEIGHT = 1920
FPS = 30

# Font discovery (Linux CI + Windows local). FFmpeg's drawtext needs a real
# font file; we probe a few common locations.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
]


def _find_font() -> Optional[str]:
    """Return the first available font file, or None."""
    for path in _FONT_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def _escape_drawtext(text: str) -> str:
    """
    Escape a string for FFmpeg's drawtext filter.

    drawtext treats ':' and '\\' specially, and single quotes must be escaped
    when the whole value is wrapped in single quotes.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")  # use a typographic apostrophe (safe)
    text = text.replace("%", "\\%")
    return text


class VideoMaker:
    """Builds vertical short videos from content packages via FFmpeg."""

    def __init__(self) -> None:
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        self.font = _find_font()
        self.output_dir = settings.content_video_dir
        self.music_path = settings.content_music_path
        self.voice = settings.content_voice
        self.enable_voice = settings.content_enable_voice

    # ------------------------------------------------------------------ #
    # Capability checks
    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        """True when FFmpeg (and a usable font) are present."""
        if not self.ffmpeg:
            logger.warning(
                "FFmpeg not found on PATH; video generation disabled. "
                "Install it (apt-get install -y ffmpeg) to enable."
            )
            return False
        if not self.font:
            logger.warning(
                "No usable font found for drawtext; video generation disabled."
            )
            return False
        return True

    # ------------------------------------------------------------------ #
    # Voiceover (optional)
    # ------------------------------------------------------------------ #
    def _make_voiceover(self, text: str, out_path: str) -> bool:
        """
        Generate an MP3 voiceover with edge-tts. Returns True on success.

        edge-tts is optional; if it is not installed we simply skip the audio
        and produce a silent (or music-only) video.
        """
        if not self.enable_voice:
            return False
        try:
            import asyncio

            import edge_tts  # type: ignore
        except Exception:  # noqa: BLE001
            logger.info("edge-tts not installed; skipping voiceover.")
            return False

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(out_path)

        try:
            asyncio.run(_run())
            return os.path.isfile(out_path) and os.path.getsize(out_path) > 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("edge-tts voiceover failed: %s", exc)
            return False

    def _audio_duration(self, path: str) -> Optional[float]:
        """Return the duration (seconds) of an audio/video file via ffprobe."""
        if not self.ffprobe or not os.path.isfile(path):
            return None
        try:
            out = subprocess.run(
                [
                    self.ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            return float(out.stdout.strip())
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    # Text layout
    # ------------------------------------------------------------------ #
    @staticmethod
    def _scenes(package: Dict) -> List[str]:
        """
        Build the ordered list of on-screen text scenes.

        Prefers the AI-provided `on_screen` list; falls back to splitting the
        script into short chunks so we always have something to render.
        """
        scenes = package.get("on_screen") or []
        scenes = [str(s).strip() for s in scenes if str(s).strip()]
        if scenes:
            return scenes

        script = str(package.get("script") or "").strip()
        if not script:
            return [str(package.get("hook") or package.get("title") or "Cinevo")]

        # Split into sentences, then group so each scene is short.
        import re

        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", script) if p.strip()]
        return parts or [script]

    @staticmethod
    def _wrap(text: str, width: int = 22) -> str:
        """Wrap text to `width` chars per line for readable on-screen text."""
        return "\n".join(textwrap.wrap(text, width=width)) or text

    # ------------------------------------------------------------------ #
    # Main build
    # ------------------------------------------------------------------ #
    def build(
        self,
        package: Dict,
        out_path: Optional[str] = None,
        seconds_per_scene: float = 3.0,
    ) -> Optional[str]:
        """
        Render `package` into an MP4 at `out_path`.

        Returns the output path on success, or None if FFmpeg is unavailable
        or the render fails.
        """
        if not self.available():
            return None

        title = str(package.get("title") or "short").strip()
        scenes = self._scenes(package)
        if not scenes:
            logger.error("No scenes to render for '%s'.", title)
            return None

        os.makedirs(self.output_dir, exist_ok=True)
        if not out_path:
            safe = "".join(c if c.isalnum() else "_" for c in title)[:40]
            out_path = os.path.join(self.output_dir, f"{safe}.mp4")

        # ---- Optional voiceover (drives total duration when present) ---- #
        voice_text = " ".join(
            [
                str(package.get("hook") or ""),
                str(package.get("script") or ""),
                str(package.get("cta") or ""),
            ]
        ).strip()

        tmpdir = tempfile.mkdtemp(prefix="cinevo_vid_")
        voice_path = os.path.join(tmpdir, "voice.mp3")
        has_voice = self._make_voiceover(voice_text, voice_path)

        # Total duration: match the voiceover when we have one, else scenes.
        if has_voice:
            dur = self._audio_duration(voice_path) or (
                len(scenes) * seconds_per_scene
            )
            # Leave a tiny tail so the last word is not cut off.
            total = max(3.0, dur + 0.4)
        else:
            total = max(3.0, len(scenes) * seconds_per_scene)

        per_scene = total / max(1, len(scenes))

        # ---- Build the drawtext filter chain ---- #
        filters: List[str] = []
        # Animated gradient background: a slow-moving color source.
        filters.append(
            f"color=c=0x0B1020:s={WIDTH}x{HEIGHT}:d={total:.2f}:r={FPS}"
        )
        # Subtle vignette-ish overlay for depth.
        filters.append(
            "drawbox=x=0:y=0:w=iw:h=ih:color=0x000000@0.25:t=fill"
        )

        font = self.font
        for idx, scene in enumerate(scenes):
            start = idx * per_scene
            end = total if idx == len(scenes) - 1 else (idx + 1) * per_scene
            wrapped = _escape_drawtext(self._wrap(scene))
            # Fade each scene in/out for a polished look.
            alpha = (
                f"if(lt(t,{start:.2f}),0,"
                f"if(lt(t,{start + 0.35:.2f}),(t-{start:.2f})/0.35,"
                f"if(lt(t,{end - 0.35:.2f}),1,"
                f"if(lt(t,{end:.2f}),({end:.2f}-t)/0.35,0))))"
            )
            filters.append(
                "drawtext="
                f"fontfile='{font}':"
                f"text='{wrapped}':"
                "fontcolor=white:fontsize=72:"
                "line_spacing=18:"
                "x=(w-text_w)/2:y=(h-text_h)/2:"
                f"alpha='{alpha}':"
                f"enable='between(t,{start:.2f},{end:.2f})'"
            )

        # Brand watermark (bottom center) for the whole video.
        brand = _escape_drawtext(settings.brand_name)
        filters.append(
            "drawtext="
            f"fontfile='{font}':"
            f"text='{brand}':"
            "fontcolor=0xFFFFFF@0.75:fontsize=44:"
            "x=(w-text_w)/2:y=h-180"
        )

        filter_complex = ",".join(filters)

        cmd: List[str] = [self.ffmpeg, "-y"]

        # Video input: the generated color source via lavfi.
        cmd += ["-f", "lavfi", "-i", f"color=c=0x0B1020:s={WIDTH}x{HEIGHT}:d={total:.2f}:r={FPS}"]

        # Audio input(s).
        if has_voice:
            cmd += ["-i", voice_path]
        elif self.music_path and os.path.isfile(self.music_path):
            cmd += ["-stream_loop", "-1", "-i", self.music_path]

        cmd += ["-filter_complex", filter_complex, "-map", "0:v"]

        if has_voice:
            cmd += ["-map", "1:a", "-shortest"]
        elif self.music_path and os.path.isfile(self.music_path):
            cmd += ["-map", "1:a", "-shortest", "-t", f"{total:.2f}"]

        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-movflags",
            "+faststart",
        ]
        if has_voice or (self.music_path and os.path.isfile(self.music_path)):
            cmd += ["-c:a", "aac", "-b:a", "128k"]

        cmd += [out_path]

        logger.info("Rendering video for '%s' (%d scenes, %.1fs)...", title, len(scenes), total)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg timed out while rendering '%s'.", title)
            shutil.rmtree(tmpdir, ignore_errors=True)
            return None

        shutil.rmtree(tmpdir, ignore_errors=True)

        if proc.returncode != 0:
            logger.error("FFmpeg failed for '%s': %s", title, proc.stderr[-1500:])
            return None

        if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            logger.error("FFmpeg produced no output for '%s'.", title)
            return None

        logger.info("Video ready: %s (%.1f KB)", out_path, os.path.getsize(out_path) / 1024)
        return out_path


# Module singleton.
video_maker = VideoMaker()
