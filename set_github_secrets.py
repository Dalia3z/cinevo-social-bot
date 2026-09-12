"""
set_github_secrets.py
=====================
Sets the YouTube OAuth GitHub Actions secrets for this repository in ONE step,
so you don't have to add them one-by-one in the GitHub web UI.

What it sets (all three MUST come from the SAME OAuth client):
    * YOUTUBE_OAUTH_CLIENT_ID
    * YOUTUBE_OAUTH_CLIENT_SECRET
    * YOUTUBE_OAUTH_REFRESH_TOKEN

How it works:
    GitHub encrypts secret values with a public key (libsodium sealed box).
    This script:
      1. Fetches the repo public key (GET /repos/{owner}/{repo}/actions/secrets/public-key).
      2. Encrypts each value with PyNaCl.
      3. PUTs each encrypted value (PUT /repos/{owner}/{repo}/actions/secrets/{name}).

Requirements:
    pip install requests pynacl

    A GitHub Personal Access Token (classic) with the `repo` + `workflow`
    scopes, OR a fine-grained token with "Secrets: Read and write".

Usage (CMD):
    python set_github_secrets.py --token ghp_your_token_here ^
        --client-id 971866242128-....apps.googleusercontent.com ^
        --client-secret GOCSPX-.... ^
        --refresh-token 1//03Kve....

    # or set the values as environment variables and run with no args:
    set GITHUB_TOKEN=ghp_your_token_here
    set YOUTUBE_OAUTH_CLIENT_ID=...
    set YOUTUBE_OAUTH_CLIENT_SECRET=...
    set YOUTUBE_OAUTH_REFRESH_TOKEN=...
    python set_github_secrets.py

Security:
    The token and secret values are only used in-memory for API calls. They are
    NEVER written to disk or committed. Revoke the GitHub token when done.
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

try:
    from nacl import encoding, public  # noqa: E402
except ImportError:  # pragma: no cover
    print("! PyNaCl is not installed. Run:  pip install pynacl")
    sys.exit(1)

OWNER = "Dalia3z"
REPO = "cinevo-social-bot"
API = "https://api.github.com"

SECRET_NAMES = (
    "YOUTUBE_OAUTH_CLIENT_ID",
    "YOUTUBE_OAUTH_CLIENT_SECRET",
    "YOUTUBE_OAUTH_REFRESH_TOKEN",
)


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_public_key(token: str) -> tuple:
    """Return (key_id, public_key_b64) for the repository."""
    url = f"{API}/repos/{OWNER}/{REPO}/actions/secrets/public-key"
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

    data = resp.json()
    return data["key_id"], data["key"]


def _encrypt(public_key_b64: str, value: str) -> str:
    """Encrypt a secret value with the repo public key (libsodium sealed box)."""
    pk = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(value.encode("utf-8"))
    return encoding.Base64Encoder().encode(encrypted).decode("utf-8")


def _put_secret(token: str, name: str, encrypted_value: str, key_id: str) -> None:
    url = f"{API}/repos/{OWNER}/{REPO}/actions/secrets/{name}"
    payload = {"encrypted_value": encrypted_value, "key_id": key_id}
    resp = requests.put(url, headers=_headers(token), json=payload, timeout=30)

    if resp.status_code in (201, 204):
        print(f"  [OK]   {name} set")
        return
    if resp.status_code == 401:
        print(f"  [FAIL] {name}: 401 Unauthorized (bad token).")
    elif resp.status_code == 403:
        print(f"  [FAIL] {name}: 403 Forbidden (token lacks 'Secrets: write').")
    elif resp.status_code == 404:
        print(f"  [FAIL] {name}: 404 Not Found (check OWNER/REPO).")
    else:
        print(f"  [FAIL] {name}: HTTP {resp.status_code} -> {resp.text[:200]}")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set the YouTube OAuth GitHub Actions secrets in one step."
    )
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN"),
                        help="GitHub PAT (or set GITHUB_TOKEN).")
    parser.add_argument("--client-id", default=os.getenv("YOUTUBE_OAUTH_CLIENT_ID"),
                        help="OAuth client id (or set YOUTUBE_OAUTH_CLIENT_ID).")
    parser.add_argument("--client-secret", default=os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET"),
                        help="OAuth client secret (or set YOUTUBE_OAUTH_CLIENT_SECRET).")
    parser.add_argument("--refresh-token", default=os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN"),
                        help="OAuth refresh token (or set YOUTUBE_OAUTH_REFRESH_TOKEN).")
    args = parser.parse_args()

    missing = [n for n, v in (
        ("--token / GITHUB_TOKEN", args.token),
        ("--client-id / YOUTUBE_OAUTH_CLIENT_ID", args.client_id),
        ("--client-secret / YOUTUBE_OAUTH_CLIENT_SECRET", args.client_secret),
        ("--refresh-token / YOUTUBE_OAUTH_REFRESH_TOKEN", args.refresh_token),
    ) if not v]
    if missing:
        print("! Missing required value(s):")
        for m in missing:
            print(f"    - {m}")
        sys.exit(1)

    print("=" * 70)
    print("Setting GitHub Actions secrets")
    print("=" * 70)
    print(f"Repository : {OWNER}/{REPO}")
    print()

    key_id, public_key_b64 = _get_public_key(args.token)
    print(f"Fetched repo public key (id={key_id[:12]}...)")
    print()

    values = {
        "YOUTUBE_OAUTH_CLIENT_ID": args.client_id,
        "YOUTUBE_OAUTH_CLIENT_SECRET": args.client_secret,
        "YOUTUBE_OAUTH_REFRESH_TOKEN": args.refresh_token,
    }

    for name in SECRET_NAMES:
        encrypted = _encrypt(public_key_b64, values[name])
        _put_secret(args.token, name, encrypted, key_id)

    print()
    print("=" * 70)
    print("DONE - all three secrets are set.")
    print("=" * 70)
    print("Next: re-run the workflow (Actions tab -> Run workflow) and confirm")
    print("the summary shows 'Replies posted: N' with N > 0.")


if __name__ == "__main__":
    main()
