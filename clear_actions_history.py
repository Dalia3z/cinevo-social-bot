"""
clear_actions_history.py
========================
Deletes ALL GitHub Actions workflow runs for this repository.

Why:
    The Actions tab keeps every past run (including the failed ones with the
    old bad API key). This script wipes that history so you start clean.

What it does:
    * Lists every workflow run via the GitHub REST API.
    * Deletes each one (DELETE /repos/{owner}/{repo}/actions/runs/{id}).
    * Prints a progress summary.

Requirements:
    A GitHub Personal Access Token (classic) with the `repo` + `workflow`
    scopes, OR a fine-grained token with "Actions: Read and write".

Usage (PowerShell / CMD):
    set GITHUB_TOKEN=ghp_your_token_here
    python clear_actions_history.py

    # or pass it inline:
    python clear_actions_history.py --token ghp_your_token_here

    # preview without deleting:
    python clear_actions_history.py --dry-run

Security:
    The token is only used in-memory for API calls. It is NEVER written to
    disk or committed. Revoke it from GitHub when you are done.
"""

import argparse
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

import requests  # noqa: E402

OWNER = "Dalia3z"
REPO = "cinevo-social-bot"
API = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _list_runs(token: str):
    """Yield every workflow run id in the repository (paginated)."""
    page = 1
    while True:
        url = f"{API}/repos/{OWNER}/{REPO}/actions/runs"
        params = {"per_page": 100, "page": page}
        resp = requests.get(url, headers=_headers(token), params=params, timeout=30)

        if resp.status_code == 401:
            print("! 401 Unauthorized: your token is invalid or expired.")
            sys.exit(1)
        if resp.status_code == 403:
            print("! 403 Forbidden: token lacks 'repo'/'workflow' scope.")
            print(f"  GitHub says: {resp.text[:300]}")
            sys.exit(1)
        if resp.status_code == 404:
            print(f"! 404 Not Found: check OWNER/REPO ({OWNER}/{REPO}).")
            sys.exit(1)
        resp.raise_for_status()

        runs = resp.json().get("workflow_runs", [])
        if not runs:
            return
        for run in runs:
            yield run["id"], run.get("name", "?"), run.get("status", "?")
        page += 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear GitHub Actions run history.")
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN", ""),
                        help="GitHub PAT (or set GITHUB_TOKEN env var).")
    parser.add_argument("--dry-run", action="store_true",
                        help="List runs without deleting them.")
    args = parser.parse_args()

    token = args.token.strip()
    if not token:
        print("! No token provided.")
        print("  Set it with:  set GITHUB_TOKEN=ghp_xxx   (then re-run)")
        print("  Or pass:      python clear_actions_history.py --token ghp_xxx")
        sys.exit(1)

    print("=" * 70)
    print(f"Clearing Actions history for {OWNER}/{REPO}")
    print("=" * 70)

    runs = list(_list_runs(token))
    if not runs:
        print("No workflow runs found. Nothing to delete.")
        return

    print(f"Found {len(runs)} workflow run(s).\n")

    if args.dry_run:
        for rid, name, status in runs:
            print(f"  [dry-run] would delete run {rid} ({name}, {status})")
        print("\nDry-run complete. Nothing was deleted.")
        return

    deleted = 0
    failed = 0
    for rid, name, status in runs:
        url = f"{API}/repos/{OWNER}/{REPO}/actions/runs/{rid}"
        resp = requests.delete(url, headers=_headers(token), timeout=30)
        if resp.status_code in (204, 200):
            deleted += 1
            print(f"  [OK]   deleted run {rid} ({name})")
        else:
            failed += 1
            print(f"  [FAIL] run {rid} -> HTTP {resp.status_code}: {resp.text[:150]}")
        # Be gentle with the API rate limit.
        time.sleep(0.3)

    print("\n" + "=" * 70)
    print(f"Done. Deleted: {deleted}   Failed: {failed}")
    print("=" * 70)
    print("Refresh the Actions tab in your browser to confirm it is empty.")


if __name__ == "__main__":
    main()
