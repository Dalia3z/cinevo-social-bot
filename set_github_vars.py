"""
set_github_vars.py
==================
Sets the CONTENT_* GitHub Actions **Variables** for this repository in ONE step,
so you don't have to add them one-by-one in the GitHub web UI.

Variables vs Secrets:
    * Variables are NOT encrypted - they are visible in the repo settings and
      in workflow logs. That is fine here: none of these values are secret.
    * Secrets (API keys, OAuth tokens) are set by set_github_secrets.py instead.

What it sets (all 8 content/video variables):
    * CONTENT_ENABLED            = true
    * CONTENT_AUTO_PUBLISH       = true
    * CONTENT_DAILY_PUBLISH_LIMIT = 1
    * CONTENT_PRIVACY            = unlisted
    * CONTENT_LANGUAGE           = en
    * CONTENT_POSTS_PER_DAY      = 1
    * CONTENT_VOICE              = en-US-AriaNeural
    * CONTENT_ENABLE_VOICE       = true

How it works:
    Variables are plain text, so no encryption is needed. This script:
      1. PUTs each value (PUT /repos/{owner}/{repo}/actions/variables/{name}).
      2. If the variable does not exist yet (404), it creates it
         (POST /repos/{owner}/{repo}/actions/variables).

Requirements:
    pip install requests

    A GitHub Personal Access Token (classic) with the `repo` + `workflow`
    scopes, OR a fine-grained token with "Variables: Read and write".

Usage (CMD):
    python set_github_vars.py --token ghp_your_token_here

    # or set the token as an environment variable and run with no args:
    set GITHUB_TOKEN=ghp_your_token_here
    python set_github_vars.py

    # override any individual value:
    python set_github_vars.py --token ghp_... --privacy public

Security:
    The token is only used in-memory for API calls. It is NEVER written to disk
    or committed. Revoke the GitHub token when done.
"""

import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

import requests  # noqa: E402

OWNER = "Dalia3z"
REPO = "cinevo-social-bot"
API = "https://api.github.com"

# The exact values the user asked for. Order is preserved for the printout.
DEFAULT_VARS = {
    "CONTENT_ENABLED": "true",
    "CONTENT_AUTO_PUBLISH": "true",
    "CONTENT_DAILY_PUBLISH_LIMIT": "1",
    "CONTENT_PRIVACY": "unlisted",
    "CONTENT_LANGUAGE": "en",
    "CONTENT_POSTS_PER_DAY": "1",
    "CONTENT_VOICE": "en-US-AriaNeural",
    "CONTENT_ENABLE_VOICE": "true",
}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _check_token(token: str) -> None:
    """Fail fast with a clear message if the token cannot see the repo."""
    url = f"{API}/repos/{OWNER}/{REPO}"
    resp = requests.get(url, headers=_headers(token), timeout=30)

    if resp.status_code == 401:
        print("! 401 Unauthorized: your GitHub token is invalid or expired.")
        sys.exit(1)
    if resp.status_code == 403:
        print("! 403 Forbidden: token lacks 'repo'/'workflow' scope.")
        print(f"  GitHub says: {resp.text[:300]}")
        sys.exit(1)
    if resp.status_code == 404:
        print(f"! 404 Not Found: check OWNER/REPO ({OWNER}/{REPO}).")
        sys.exit(1)
    resp.raise_for_status()


def _put_variable(token: str, name: str, value: str) -> None:
    """Create or update a repository Actions variable."""
    url = f"{API}/repos/{OWNER}/{REPO}/actions/variables/{name}"
    payload = {"name": name, "value": value}

    resp = requests.patch(url, headers=_headers(token), json=payload, timeout=30)

    # 204 = updated. 404 = does not exist yet -> create it.
    if resp.status_code == 204:
        print(f"  [OK]   {name} = {value}")
        return
    if resp.status_code == 404:
        create_url = f"{API}/repos/{OWNER}/{REPO}/actions/variables"
        resp = requests.post(create_url, headers=_headers(token), json=payload,
                             timeout=30)
        if resp.status_code == 201:
            print(f"  [OK]   {name} = {value}  (created)")
            return

    if resp.status_code == 401:
        print(f"  [FAIL] {name}: 401 Unauthorized (bad token).")
    elif resp.status_code == 403:
        print(f"  [FAIL] {name}: 403 Forbidden (token lacks 'Variables: write').")
    elif resp.status_code == 404:
        print(f"  [FAIL] {name}: 404 Not Found (check OWNER/REPO).")
    else:
        print(f"  [FAIL] {name}: HTTP {resp.status_code} -> {resp.text[:200]}")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set the CONTENT_* GitHub Actions variables in one step."
    )
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN"),
                        help="GitHub PAT (or set GITHUB_TOKEN).")
    parser.add_argument("--enabled", default=DEFAULT_VARS["CONTENT_ENABLED"],
                        help="CONTENT_ENABLED (default: true).")
    parser.add_argument("--auto-publish", default=DEFAULT_VARS["CONTENT_AUTO_PUBLISH"],
                        help="CONTENT_AUTO_PUBLISH (default: true).")
    parser.add_argument("--daily-limit", default=DEFAULT_VARS["CONTENT_DAILY_PUBLISH_LIMIT"],
                        help="CONTENT_DAILY_PUBLISH_LIMIT (default: 1).")
    parser.add_argument("--privacy", default=DEFAULT_VARS["CONTENT_PRIVACY"],
                        choices=["public", "unlisted", "private"],
                        help="CONTENT_PRIVACY (default: unlisted).")
    parser.add_argument("--language", default=DEFAULT_VARS["CONTENT_LANGUAGE"],
                        help="CONTENT_LANGUAGE (default: en).")
    parser.add_argument("--posts-per-day", default=DEFAULT_VARS["CONTENT_POSTS_PER_DAY"],
                        help="CONTENT_POSTS_PER_DAY (default: 1).")
    parser.add_argument("--voice", default=DEFAULT_VARS["CONTENT_VOICE"],
                        help="CONTENT_VOICE (default: en-US-AriaNeural).")
    parser.add_argument("--enable-voice", default=DEFAULT_VARS["CONTENT_ENABLE_VOICE"],
                        help="CONTENT_ENABLE_VOICE (default: true).")
    args = parser.parse_args()

    if not args.token:
        print("! Missing GitHub token. Pass --token ghp_... or set GITHUB_TOKEN.")
        sys.exit(1)

    values = {
        "CONTENT_ENABLED": args.enabled,
        "CONTENT_AUTO_PUBLISH": args.auto_publish,
        "CONTENT_DAILY_PUBLISH_LIMIT": args.daily_limit,
        "CONTENT_PRIVACY": args.privacy,
        "CONTENT_LANGUAGE": args.language,
        "CONTENT_POSTS_PER_DAY": args.posts_per_day,
        "CONTENT_VOICE": args.voice,
        "CONTENT_ENABLE_VOICE": args.enable_voice,
    }

    print("=" * 70)
    print("Setting GitHub Actions variables")
    print("=" * 70)
    print(f"Repository : {OWNER}/{REPO}")
    print()

    _check_token(args.token)
    print("Token OK - repository reachable.")
    print()

    for name, value in values.items():
        _put_variable(args.token, name, value)

    print()
    print("=" * 70)
    print("DONE - all 8 content variables are set.")
    print("=" * 70)
    print("Next steps:")
    print("  1. Actions tab -> 'Cinevo Content (auto video)' -> Run workflow")
    print("  2. First run: set dry_run = true  (nothing is published)")
    print("  3. Then:      set no_publish = true (video is built, not uploaded)")
    print("  4. Finally:   leave both false      (uploads as 'unlisted')")


if __name__ == "__main__":
    main()
