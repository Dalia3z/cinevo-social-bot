"""
check_setup.py
==============
Local, step-by-step diagnostic for the Cinevo bot.

Run this BEFORE deploying to GitHub Actions to confirm every credential
works 100%. It tests each piece independently and prints a clear PASS/FAIL
for each one, so you know exactly what to fix.

Usage:
    python check_setup.py

It checks, in order:
    1. .env file exists and is readable
    2. Required variables are present (not empty)
    3. DeepSeek API key works (real API call)
    4. YouTube API key works (real API call)
    5. YouTube OAuth token works (real API call)
    6. targets.json has at least one real target
    7. A full dry-run cycle (generate a reply, do NOT post)

Nothing is posted to any platform. Safe to run repeatedly.
"""

import json
import os
import sys

# Make sure we can import the project modules regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows consoles default to cp1252 and crash on emoji / non-ASCII output.
# Force UTF-8 on stdout/stderr so this script runs on any terminal.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - very old Python
    pass

import requests  # noqa: E402

# --------------------------------------------------------------------------- #
# Tiny test harness
# --------------------------------------------------------------------------- #
PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

_results = []


def _record(name: str, status: str, detail: str = "") -> None:
    _results.append((name, status, detail))
    icon = {"PASS": "[OK]  ", "FAIL": "[FAIL]", "WARN": "[WARN]"}[status]
    print(f"{icon} {name}")
    if detail:
        for line in detail.splitlines():
            print(f"        {line}")


def _section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# --------------------------------------------------------------------------- #
# 1. .env file
# --------------------------------------------------------------------------- #
def check_env_file() -> bool:
    _section("1) Checking .env file")
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        _record(
            ".env file exists",
            FAIL,
            f"Not found at: {env_path}\n"
            "Fix: copy .env.example to .env and fill in your real keys:\n"
            "     copy .env.example .env",
        )
        return False
    _record(".env file exists", PASS, env_path)
    return True


# --------------------------------------------------------------------------- #
# 2. Required variables
# --------------------------------------------------------------------------- #
def check_required_vars() -> bool:
    _section("2) Checking required variables")
    from config import settings

    ok = True

    if settings.deepseek_api_key:
        _record("DEEPSEEK_API_KEY is set", PASS)
    else:
        _record("DEEPSEEK_API_KEY is set", FAIL, "Missing. Add it to .env")
        ok = False

    if settings.active_platforms:
        _record(
            "ACTIVE_PLATFORMS is set",
            PASS,
            f"value = {','.join(settings.active_platforms)}",
        )
    else:
        _record("ACTIVE_PLATFORMS is set", FAIL, "Missing. Add e.g. youtube")
        ok = False

    if "youtube" in settings.active_platforms:
        if settings.youtube_api_key:
            _record("YOUTUBE_API_KEY is set", PASS)
        else:
            _record("YOUTUBE_API_KEY is set", FAIL, "Missing. Add it to .env")
            ok = False

        if settings.youtube_oauth_token:
            _record("YOUTUBE_OAUTH_TOKEN is set", PASS)
        else:
            _record(
                "YOUTUBE_OAUTH_TOKEN is set",
                WARN,
                "Missing. The bot can READ comments but CANNOT POST replies.",
            )

    return ok


# --------------------------------------------------------------------------- #
# 3. DeepSeek API
# --------------------------------------------------------------------------- #
def check_deepseek() -> bool:
    _section("3) Testing DeepSeek API key")
    from config import settings

    if not settings.deepseek_api_key:
        _record("DeepSeek API call", FAIL, "Skipped: no API key.")
        return False

    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "user", "content": "Reply with exactly: OK"},
        ],
        "max_tokens": 5,
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
    except requests.exceptions.RequestException as exc:
        _record("DeepSeek API call", FAIL, f"Network error: {exc}")
        return False

    if resp.status_code == 200:
        try:
            content = resp.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError):
            content = "(could not parse response)"
        _record("DeepSeek API call", PASS, f"Model replied: {content!r}")
        return True

    _record(
        "DeepSeek API call",
        FAIL,
        f"HTTP {resp.status_code}\n{resp.text[:500]}",
    )
    return False


# --------------------------------------------------------------------------- #
# 4. YouTube API key
# --------------------------------------------------------------------------- #
def check_youtube_key() -> bool:
    _section("4) Testing YouTube API key")
    from config import settings

    if not settings.youtube_api_key:
        _record("YouTube API key", FAIL, "Skipped: no API key.")
        return False

    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "id",
        "chart": "mostPopular",
        "maxResults": 1,
        "key": settings.youtube_api_key,
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
    except requests.exceptions.RequestException as exc:
        _record("YouTube API key", FAIL, f"Network error: {exc}")
        return False

    if resp.status_code == 200:
        _record(
            "YouTube API key",
            PASS,
            "Key is valid and YouTube Data API v3 is enabled.",
        )
        return True

    _record(
        "YouTube API key",
        FAIL,
        f"HTTP {resp.status_code}\nGoogle says: {resp.text[:500]}\n"
        "Common fixes:\n"
        "  - Enable 'YouTube Data API v3' in Google Cloud Console\n"
        "  - Remove HTTP referrer / IP restrictions from the key\n"
        "  - Make sure the key is copied correctly (no spaces/quotes)",
    )
    return False


# --------------------------------------------------------------------------- #
# 5. YouTube OAuth token
# --------------------------------------------------------------------------- #
def check_youtube_oauth() -> bool:
    _section("5) Testing YouTube OAuth token")
    from config import settings

    if not settings.youtube_oauth_token:
        _record("YouTube OAuth token", WARN, "Skipped: no token (cannot post).")
        return False

    # A cheap authenticated call: list the authenticated user's channel.
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {"part": "snippet", "mine": "true"}
    headers = {"Authorization": f"Bearer {settings.youtube_oauth_token}"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
    except requests.exceptions.RequestException as exc:
        _record("YouTube OAuth token", FAIL, f"Network error: {exc}")
        return False

    if resp.status_code == 200:
        items = resp.json().get("items", [])
        title = items[0]["snippet"]["title"] if items else "(no channel)"
        _record("YouTube OAuth token", PASS, f"Authenticated as: {title}")
        return True

    _record(
        "YouTube OAuth token",
        FAIL,
        f"HTTP {resp.status_code}\nGoogle says: {resp.text[:500]}\n"
        "Note: OAuth access tokens expire (~1 hour). You may need to refresh it.",
    )
    return False


# --------------------------------------------------------------------------- #
# 6. targets.json
# --------------------------------------------------------------------------- #
def check_targets() -> bool:
    _section("6) Checking targets.json")
    import targets_loader

    data = targets_loader.load_targets()
    t = targets_loader.Targets(data)
    channels = t.youtube_channels
    videos = t.youtube_videos
    total = len(channels) + len(videos)

    if total == 0:
        _record(
            "targets.json has targets",
            FAIL,
            "No YouTube targets found.\n"
            "Fix: run  python discover_targets.py --all --max 10000",
        )
        return False

    _record(
        "targets.json has targets",
        PASS,
        f"channels={len(channels)} videos={len(videos)}",
    )
    return True


# --------------------------------------------------------------------------- #
# 7. Full dry-run cycle
# --------------------------------------------------------------------------- #
def check_dry_run() -> bool:
    _section("7) Running a full dry-run cycle (nothing is posted)")
    from main import EngagementBot

    try:
        bot = EngagementBot(dry_run=True)
    except Exception as exc:  # noqa: BLE001
        _record("Dry-run cycle", FAIL, f"Could not build bot: {exc}")
        return False

    if not bot.handlers:
        _record("Dry-run cycle", FAIL, "No platform handlers enabled.")
        return False

    try:
        posted = bot.run_once()
    except Exception as exc:  # noqa: BLE001
        _record("Dry-run cycle", FAIL, f"Cycle crashed: {exc}")
        return False

    _record(
        "Dry-run cycle",
        PASS,
        f"Completed. Items handled (dry-run): {posted}",
    )
    return True


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    print("\n" + "#" * 70)
    print("# Cinevo Bot - Local Setup Check")
    print("#" * 70)

    if not check_env_file():
        _summary()
        sys.exit(1)

    check_required_vars()
    check_deepseek()
    check_youtube_key()
    check_youtube_oauth()
    check_targets()
    check_dry_run()

    _summary()


def _summary() -> None:
    _section("SUMMARY")
    fails = [r for r in _results if r[1] == FAIL]
    warns = [r for r in _results if r[1] == WARN]
    passes = [r for r in _results if r[1] == PASS]

    print(f"  PASS: {len(passes)}")
    print(f"  WARN: {len(warns)}")
    print(f"  FAIL: {len(fails)}")

    if fails:
        print("\n  Fix these FAIL items before deploying:")
        for name, _, detail in fails:
            print(f"    - {name}")
        print("\n  Result: NOT READY [X]")
    elif warns:
        print("\n  Result: READY (with warnings) [!]")
    else:
        print("\n  Result: READY 100% [OK]")
    print()


if __name__ == "__main__":
    main()
