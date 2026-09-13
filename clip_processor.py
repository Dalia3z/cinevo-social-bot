"""
clip_processor.py
=================
Cuts short clips out of a downloaded trailer and applies TRANSFORMATIONS to
them, so the final Short is not a straight re-upload of the source footage.

WHY TRANSFORM?
    A raw re-upload of a trailer is the easiest thing for Content ID to match.
    Every transformation below changes the fingerprint of the footage:

      * Ken Burns   - a slow zoom/pan, so the frame is never static
      * Colour grade- a cinematic teal/orange LUT-ish curve + contrast
      * Mirror      - horizontal flip (very effective, cheap)
      * Speed ramp  - subtle 1.05x-1.15x speed change (also shifts audio pitch)
      * Crop/scale  - 9:16 vertical crop with a blurred background fill

    None of this makes the content "yours" legally. It only reduces the
    probability of an automated match. See README for the full discussion.

DESIGN
    * FFmpeg is invoked as a SUBPROCESS. A missing binary degrades gracefully
      (`available()` returns False) instead of crashing the pipeline.
    * Every clip is written to disk and cached: re-running does not re-encode.
    * All transforms are individually toggleable via config so you can dial
      the aggressiveness up or down without touching code.

USAGE (CLI)
    python clip_processor.py --input trailer_clips/Sinister.mp4 --count 5
    python clip_processor.py --input trailer_clips/Sinister.mp4 --list
    python clip_processor.py --check

USAGE (code)
    from clip_processor import clip_processor
    clips = clip_processor.process(Path("trailer_clips/Sinister.mp4"), count=5)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:  # Windows consoles are cp1252 by default; keep logs readable.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

from config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output geometry (vertical Shorts)
# ---------------------------------------------------------------------------

OUT_WIDTH = 1080
OUT_HEIGHT = 1920
OUT_FPS = 30

#: Skip the first N seconds of a trailer: the studio logo / rating card is
#: boring and is also the most likely part to be matched.
SKIP_HEAD_SECONDS = 8.0

#: Never cut a clip from the last N seconds (end cards / release dates).
SKIP_TAIL_SECONDS = 5.0

#: Colour-grade presets. Each is a chain of FFmpeg filters applied to the
#: video stream. Chosen to look "cinematic" without destroying the image.
COLOR_GRADES: Dict[str, str] = {
    "teal_orange": (
        "curves=r='0/0 0.5/0.55 1/1':b='0/0.05 0.5/0.5 1/0.95',"
        "eq=contrast=1.12:saturation=1.18:brightness=0.01"
    ),
    "warm_film": (
        "colorbalance=rs=0.06:gs=0.02:bs=-0.06,"
        "eq=contrast=1.10:saturation=1.12"
    ),
    "cold_thriller": (
        "colorbalance=rs=-0.05:gs=0.0:bs=0.08,"
        "eq=contrast=1.15:saturation=0.95:brightness=-0.01"
    ),
    "punchy": "eq=contrast=1.20:saturation=1.30:gamma=1.05",
}

#: Speed-ramp factors. Slightly off 1.0 so the audio fingerprint shifts too.
SPEED_FACTORS: Tuple[float, ...] = (1.05, 1.08, 1.12, 0.95)


def _find_ffmpeg() -> Optional[str]:
    """Locate the ffmpeg binary (PATH first, then common Windows spots)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in (
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def _find_ffprobe() -> Optional[str]:
    """Locate the ffprobe binary (ships alongside ffmpeg)."""
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = _find_ffmpeg()
    if ffmpeg:
        probe = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
        for ext in ("", ".exe"):
            if os.path.isfile(probe + ext):
                return probe + ext
    return None


def _safe_name(text: str, max_len: int = 60) -> str:
    """Turn an arbitrary title into a filesystem-safe stem."""
    text = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return (text[:max_len] or "clip").strip("_")


@dataclass
class ClipInfo:
    """Metadata about one processed clip."""

    path: Path
    source: str = ""
    start: float = 0.0
    duration: float = 0.0
    grade: str = ""
    speed: float = 1.0
    mirrored: bool = False
    ken_burns: bool = False
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "path": str(self.path),
            "source": self.source,
            "start": self.start,
            "duration": self.duration,
            "grade": self.grade,
            "speed": self.speed,
            "mirrored": self.mirrored,
            "ken_burns": self.ken_burns,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ClipInfo":
        return cls(
            path=Path(data["path"]),
            source=data.get("source", ""),
            start=float(data.get("start", 0.0)),
            duration=float(data.get("duration", 0.0)),
            grade=data.get("grade", ""),
            speed=float(data.get("speed", 1.0)),
            mirrored=bool(data.get("mirrored", False)),
            ken_burns=bool(data.get("ken_burns", False)),
            tags=list(data.get("tags", [])),
        )


class ClipProcessor:
    """
    Cuts and transforms short clips out of a source trailer.

    The processor is deliberately conservative: if FFmpeg is missing, or the
    source is unreadable, it returns an empty list rather than raising. The
    orchestrator can then fall back to the text-only video maker.
    """

    def __init__(self, output_dir: Optional[str] = None) -> None:
        self._ffmpeg = _find_ffmpeg()
        self._ffprobe = _find_ffprobe()
        self._output_dir = Path(
            output_dir or getattr(settings, "trailer_output_dir", "trailer_output")
        )

    # ------------------------------------------------------------------ #
    # Capability / paths
    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        """True when FFmpeg is present and the trailer pipeline is enabled."""
        if not getattr(settings, "trailer_enabled", False):
            logger.debug("ClipProcessor: TRAILER_ENABLED is false.")
            return False
        if not self._ffmpeg:
            logger.warning("ClipProcessor: ffmpeg not found on PATH.")
            return False
        return True

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def _ensure_output_dir(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _clip_path(self, stem: str, index: int) -> Path:
        return self._output_dir / f"{stem}_clip{index:02d}.mp4"

    def _meta_path(self, stem: str, index: int) -> Path:
        return self._output_dir / f"{stem}_clip{index:02d}.json"

    # ------------------------------------------------------------------ #
    # Probing
    # ------------------------------------------------------------------ #
    def probe(self, path: Path) -> Dict:
        """
        Return {duration, width, height} for a media file.

        Uses ffprobe when available; falls back to a regex over ffmpeg's
        stderr banner (which prints "Duration: 00:02:31.04").
        """
        info = {"duration": 0.0, "width": 0, "height": 0}
        if not path or not Path(path).is_file():
            return info

        if self._ffprobe:
            cmd = [
                self._ffprobe,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "json",
                str(path),
            ]
            try:
                out = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60
                )
                if out.returncode == 0 and out.stdout.strip():
                    data = json.loads(out.stdout)
                    streams = data.get("streams") or [{}]
                    info["width"] = int(streams[0].get("width") or 0)
                    info["height"] = int(streams[0].get("height") or 0)
                    fmt = data.get("format") or {}
                    info["duration"] = float(fmt.get("duration") or 0.0)
                    if info["duration"]:
                        return info
            except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
                logger.debug("ffprobe failed for %s; falling back.", path)

        # Fallback: parse ffmpeg's banner.
        if self._ffmpeg:
            try:
                out = subprocess.run(
                    [self._ffmpeg, "-i", str(path)],
                    capture_output=True, text=True, timeout=60,
                )
                blob = (out.stderr or "") + (out.stdout or "")
                m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", blob)
                if m:
                    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    info["duration"] = h * 3600 + mi * 60 + s
                m = re.search(r"(\d{2,5})x(\d{2,5})", blob)
                if m:
                    info["width"] = int(m.group(1))
                    info["height"] = int(m.group(2))
            except (subprocess.SubprocessError, ValueError):
                logger.debug("ffmpeg banner parse failed for %s.", path)
        return info

    # ------------------------------------------------------------------ #
    # Filter graph construction
    # ------------------------------------------------------------------ #
    def _vertical_filter(self, ken_burns: bool, duration: float) -> str:
        """
        Build the scale/crop chain that turns any source into 1080x1920.

        Strategy: scale the source so it COVERS the 9:16 frame, then crop the
        centre. When Ken Burns is on we instead scale slightly larger and use
        a slow zoompan so the frame is never static.
        """
        if ken_burns:
            # zoompan works on a per-frame basis; d=1 keeps it in sync with
            # the input frame rate, and the zoom expression ramps 1.0 -> 1.12.
            frames = max(int(duration * OUT_FPS), 1)
            return (
                f"scale={OUT_WIDTH * 2}:{OUT_HEIGHT * 2}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={OUT_WIDTH * 2}:{OUT_HEIGHT * 2},"
                f"zoompan=z='min(zoom+0.0008,1.12)':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={OUT_WIDTH}x{OUT_HEIGHT}:fps={OUT_FPS}"
            )
        return (
            f"scale={OUT_WIDTH}:{OUT_HEIGHT}:"
            f"force_original_aspect_ratio=increase,"
            f"crop={OUT_WIDTH}:{OUT_HEIGHT}"
        )

    def _build_filter(
        self,
        duration: float,
        grade: str,
        mirror: bool,
        ken_burns: bool,
    ) -> str:
        """Assemble the full -vf filter chain for one clip."""
        parts: List[str] = [self._vertical_filter(ken_burns, duration)]

        if mirror:
            parts.append("hflip")

        grade_chain = COLOR_GRADES.get(grade)
        if grade_chain:
            parts.append(grade_chain)

        # A subtle vignette adds a "cinematic" feel and further alters the
        # pixel statistics of the frame.
        parts.append("vignette=PI/5")

        # Normalise the frame rate last so every clip is identical.
        parts.append(f"fps={OUT_FPS}")
        parts.append("format=yuv420p")

        return ",".join(parts)

    # ------------------------------------------------------------------ #
    # Cutting
    # ------------------------------------------------------------------ #
    def _cut(
        self,
        source: Path,
        out_path: Path,
        start: float,
        duration: float,
        grade: str,
        mirror: bool,
        ken_burns: bool,
        speed: float,
    ) -> bool:
        """Run one FFmpeg encode. Returns True on success."""
        if not self._ffmpeg:
            return False

        vf = self._build_filter(duration, grade, mirror, ken_burns)

        cmd: List[str] = [
            self._ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            # -ss BEFORE -i is a fast (keyframe) seek; accurate enough for
            # clips and dramatically faster than decoding from the start.
            "-ss", f"{start:.3f}",
            "-i", str(source),
            "-t", f"{duration:.3f}",
            "-vf", vf,
        ]

        # Speed ramp: atempo handles 0.5-2.0 and is pitch-preserving; we pair
        # it with setpts so video and audio stay in sync.
        if abs(speed - 1.0) > 0.001:
            cmd += ["-filter:a", f"atempo={speed:.3f}"]
            cmd += ["-filter:v", vf + f",setpts={1.0 / speed:.4f}*PTS"]
            # Remove the earlier -vf so we do not apply the chain twice.
            idx = cmd.index("-vf")
            del cmd[idx:idx + 2]

        cmd += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-movflags", "+faststart",
            str(out_path),
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
        except subprocess.SubprocessError as exc:
            logger.warning("ffmpeg failed for %s: %s", out_path.name, exc)
            return False

        if result.returncode != 0:
            logger.warning(
                "ffmpeg returned %s for %s: %s",
                result.returncode,
                out_path.name,
                (result.stderr or "").strip()[:400],
            )
            return False

        return out_path.is_file() and out_path.stat().st_size > 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def process(
        self,
        source: Path,
        count: Optional[int] = None,
        force: bool = False,
        seed: Optional[int] = None,
    ) -> List[ClipInfo]:
        """
        Cut `count` transformed clips out of `source`.

        Returns a list of ClipInfo (possibly empty). Never raises.
        """
        source = Path(source)
        if not source.is_file():
            logger.warning("ClipProcessor: source not found: %s", source)
            return []

        if not self.available():
            return []

        count = count or getattr(settings, "trailer_clips_per_video", 5)
        clip_seconds = float(getattr(settings, "trailer_clip_seconds", 3.5))

        meta = self.probe(source)
        total = float(meta.get("duration") or 0.0)
        if total <= 0:
            logger.warning("ClipProcessor: could not read duration of %s", source)
            return []

        # Usable window: skip the head (logos) and the tail (end cards).
        usable_start = min(SKIP_HEAD_SECONDS, max(total * 0.1, 0.0))
        usable_end = max(total - SKIP_TAIL_SECONDS, usable_start + clip_seconds)
        if usable_end - usable_start < clip_seconds:
            # Very short source: just use whatever is there.
            usable_start, usable_end = 0.0, total

        rng = random.Random(seed)
        stem = _safe_name(source.stem)
        self._ensure_output_dir()

        grades = list(COLOR_GRADES.keys())
        use_kb = bool(getattr(settings, "trailer_ken_burns", True))
        use_grade = bool(getattr(settings, "trailer_color_grade", True))
        use_mirror = bool(getattr(settings, "trailer_mirror", False))
        use_speed = bool(getattr(settings, "trailer_speed_ramp", False))

        clips: List[ClipInfo] = []
        attempts = 0
        max_attempts = count * 4  # avoid an infinite loop on a bad source

        while len(clips) < count and attempts < max_attempts:
            attempts += 1
            index = len(clips) + 1
            out_path = self._clip_path(stem, index)
            meta_path = self._meta_path(stem, index)

            # Reuse a cached clip unless a re-encode was requested.
            if not force and out_path.is_file() and out_path.stat().st_size > 0:
                if meta_path.is_file():
                    try:
                        clips.append(
                            ClipInfo.from_dict(
                                json.loads(meta_path.read_text(encoding="utf-8"))
                            )
                        )
                        continue
                    except (ValueError, KeyError, OSError):
                        logger.debug("Bad clip sidecar %s; re-encoding.", meta_path)

            latest_start = max(usable_end - clip_seconds, usable_start)
            start = rng.uniform(usable_start, latest_start) if latest_start > usable_start else usable_start

            grade = rng.choice(grades) if use_grade else ""
            mirror = use_mirror and rng.random() < 0.5
            ken_burns = use_kb
            speed = rng.choice(SPEED_FACTORS) if use_speed else 1.0

            logger.info(
                "Cutting clip %s/%s from %s @ %.1fs (grade=%s mirror=%s kb=%s speed=%.2f)",
                index, count, source.name, start,
                grade or "none", mirror, ken_burns, speed,
            )

            if not self._cut(
                source, out_path, start, clip_seconds,
                grade, mirror, ken_burns, speed,
            ):
                continue

            info = ClipInfo(
                path=out_path,
                source=str(source),
                start=round(start, 3),
                duration=clip_seconds,
                grade=grade,
                speed=speed,
                mirrored=mirror,
                ken_burns=ken_burns,
                tags=[t for t in (grade, "mirror" if mirror else "", "kenburns" if ken_burns else "") if t],
            )
            try:
                meta_path.write_text(
                    json.dumps(info.to_dict(), indent=2), encoding="utf-8"
                )
            except OSError:
                logger.debug("Could not write clip sidecar %s", meta_path)

            clips.append(info)

        logger.info(
            "ClipProcessor: produced %s/%s clips from %s",
            len(clips), count, source.name,
        )
        return clips

    def list_clips(self) -> List[ClipInfo]:
        """Return every clip sidecar currently on disk."""
        if not self._output_dir.is_dir():
            return []
        clips: List[ClipInfo] = []
        for meta_path in sorted(self._output_dir.glob("*.json")):
            try:
                clips.append(
                    ClipInfo.from_dict(
                        json.loads(meta_path.read_text(encoding="utf-8"))
                    )
                )
            except (ValueError, KeyError, OSError):
                continue
        return clips

    def clean(self) -> int:
        """Delete every generated clip. Returns the number removed."""
        if not self._output_dir.is_dir():
            return 0
        removed = 0
        for path in list(self._output_dir.glob("*.mp4")) + list(
            self._output_dir.glob("*.json")
        ):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed


# A single, importable instance used across the whole application.
clip_processor = ClipProcessor()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cmd_check() -> int:
    proc = ClipProcessor()
    print(f"ffmpeg : {proc._ffmpeg or 'NOT FOUND'}")
    print(f"ffprobe: {proc._ffprobe or 'NOT FOUND'}")
    print(f"enabled: {getattr(settings, 'trailer_enabled', False)}")
    print(f"output : {proc.output_dir}")
    print(f"grades : {', '.join(COLOR_GRADES)}")
    return 0 if proc._ffmpeg else 1


def _cmd_process(args: argparse.Namespace) -> int:
    proc = ClipProcessor()
    if not proc.available():
        print("ClipProcessor is not available (ffmpeg missing or TRAILER_ENABLED=false).")
        return 1
    clips = proc.process(
        Path(args.input), count=args.count, force=args.force, seed=args.seed
    )
    if not clips:
        print("No clips were produced.")
        return 1
    for clip in clips:
        print(f"{clip.path}  start={clip.start}s grade={clip.grade or 'none'} "
              f"mirror={clip.mirrored} speed={clip.speed}")
    return 0


def _cmd_list() -> int:
    clips = ClipProcessor().list_clips()
    if not clips:
        print("No clips found.")
        return 0
    for clip in clips:
        print(f"{clip.path}  ({clip.duration}s, grade={clip.grade or 'none'})")
    return 0


def _cmd_clean() -> int:
    removed = ClipProcessor().clean()
    print(f"Removed {removed} file(s).")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cut and transform short clips out of a trailer."
    )
    sub = parser.add_subparsers(dest="command")

    p_check = sub.add_parser("check", help="Show ffmpeg availability and settings.")
    p_check.set_defaults(func=lambda a: _cmd_check())

    p_proc = sub.add_parser("process", help="Cut clips from a source video.")
    p_proc.add_argument("--input", required=True, help="Source trailer path.")
    p_proc.add_argument("--count", type=int, default=None, help="How many clips.")
    p_proc.add_argument("--force", action="store_true", help="Re-encode cached clips.")
    p_proc.add_argument("--seed", type=int, default=None, help="Deterministic cuts.")
    p_proc.set_defaults(func=_cmd_process)

    p_list = sub.add_parser("list", help="List generated clips.")
    p_list.set_defaults(func=lambda a: _cmd_list())

    p_clean = sub.add_parser("clean", help="Delete generated clips.")
    p_clean.set_defaults(func=lambda a: _cmd_clean())

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    raise SystemExit(main())
