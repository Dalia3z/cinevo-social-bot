"""
get_youtube_refresh_token.py
============================
Local helper to obtain a YouTube OAuth 2.0 REFRESH TOKEN without relying on
Google's OAuth 2.0 Playground.

Why this exists
---------------
The OAuth Playground requires you to register
`https://developers.google.com/oauthplayground` as an authorized redirect URI
on your OAuth client, and it can produce `unauthorized_client` errors when the
refresh token and the client id/secret do not come from the SAME client.

This script runs the standard "installed app" loopback flow locally:
    1. It prints an authorization URL.
    2. You open it in a browser and approve access.
    3. Google redirects to http://localhost:<port>/ with a `code`.
    4. The script exchanges that code for tokens and prints the REFRESH TOKEN.

The refresh token it prints is guaranteed to belong to the SAME client id/secret
you pass in, so the bot's refresh call will work.

Prerequisites
-------------
* A Google Cloud OAuth 2.0 client of type **Desktop app** (recommended), OR a
  **Web application** client with `http://localhost` added to its authorized
  redirect URIs.
* The YouTube Data API v3 enabled for that project.

Usage
-----
    python get_youtube_refresh_token.py --client-id YOUR_ID --client-secret YOUR_SECRET

    # Optional: choose a different local port (default 8080)
    python get_youtube_refresh_token.py --client-id ... --client-secret ... --port 8090

Then copy the printed refresh token into the GitHub secret
`YOUTUBE_OAUTH_REFRESH_TOKEN`, and make sure `YOUTUBE_OAUTH_CLIENT_ID` /
`YOUTUBE_OAUTH_CLIENT_SECRET` hold the SAME values you passed here.
"""

import argparse
import http.server
import json
import socketserver
import sys
import threading
import urllib.parse
import webbrowser

import requests

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"

# The authorization code is delivered to the loopback redirect as a query param.
_auth_code_holder = {"code": None, "error": None}


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Tiny HTTP handler that captures the `code` query parameter."""

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _auth_code_holder["code"] = params["code"][0]
            message = (
                "<h2>Authorization successful</h2>"
                "<p>You can close this tab and return to the terminal.</p>"
            )
        elif "error" in params:
            _auth_code_holder["error"] = params["error"][0]
            message = (
                f"<h2>Authorization failed</h2><p>{params['error'][0]}</p>"
            )
        else:
            message = "<h2>Waiting for authorization...</h2>"

        body = f"<html><body>{message}</body></html>".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence default request logging
        return


def _run_server(port: int) -> socketserver.TCPServer:
    """Start the loopback server in a background thread."""
    server = socketserver.TCPServer(("127.0.0.1", port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Obtain a YouTube OAuth 2.0 refresh token via a local loopback flow."
    )
    parser.add_argument("--client-id", required=True, help="OAuth 2.0 Client ID")
    parser.add_argument(
        "--client-secret", required=True, help="OAuth 2.0 Client Secret"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local port for the redirect URI (default: 8080)",
    )
    parser.add_argument(
        "--redirect-uri",
        default=None,
        help=(
            "Exact redirect URI registered on your OAuth client. "
            "Defaults to http://localhost:<port>/ . Use this if your client "
            "is a 'Web application' and you registered a different URI."
        ),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not try to open the browser automatically.",
    )
    args = parser.parse_args()

    redirect_uri = args.redirect_uri or f"http://localhost:{args.port}/"

    # 1) Start the loopback server BEFORE opening the browser so we never miss
    #    the redirect.
    try:
        server = _run_server(args.port)
    except OSError as exc:
        print(f"[ERROR] Could not bind to port {args.port}: {exc}")
        print("        Try a different port with --port 8090")
        return 1

    # 2) Build the authorization URL.
    auth_params = {
        "client_id": args.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",  # required to receive a refresh token
        "prompt": "consent",  # force a refresh token even on re-auth
    }
    auth_url = f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(auth_params)}"

    print("=" * 70)
    print("YouTube OAuth 2.0 refresh-token helper")
    print("=" * 70)
    print(f"Redirect URI : {redirect_uri}")
    print(f"Scope        : {SCOPE}")
    print()
    print("IMPORTANT: this EXACT redirect URI must be registered on your OAuth")
    print("client, otherwise Google returns 'Error 400: redirect_uri_mismatch'.")
    print()
    print("How to register it:")
    print("  1. Open https://console.cloud.google.com/apis/credentials")
    print("  2. Click your OAuth 2.0 Client ID")
    print("  3. Under 'Authorized redirect URIs' click 'ADD URI'")
    print(f"  4. Paste EXACTLY: {redirect_uri}")
    print("  5. Click SAVE, wait ~1 minute, then re-run this script.")
    print()
    print("NOTE: if your client is of type 'Desktop app', localhost is allowed")
    print("      automatically and you can ignore the steps above.")
    print()
    print("Open this URL in your browser and approve access:")
    print()
    print(auth_url)
    print()

    if not args.no_browser:
        try:
            webbrowser.open(auth_url)
        except Exception:  # noqa: BLE001
            pass

    print("Waiting for the redirect... (Ctrl+C to cancel)")

    # 3) Wait for the code.
    try:
        while _auth_code_holder["code"] is None and _auth_code_holder["error"] is None:
            threading.Event().wait(0.5)
    except KeyboardInterrupt:
        print("\nCancelled.")
        server.shutdown()
        return 1

    server.shutdown()

    if _auth_code_holder["error"]:
        print(f"[ERROR] Google returned an error: {_auth_code_holder['error']}")
        return 1

    code = _auth_code_holder["code"]
    print("Authorization code received. Exchanging it for tokens...")

    # 4) Exchange the code for tokens.
    token_payload = {
        "code": code,
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    resp = requests.post(TOKEN_ENDPOINT, data=token_payload, timeout=30)

    if resp.status_code != 200:
        print(f"[ERROR] Token exchange failed (HTTP {resp.status_code}):")
        print(resp.text)
        return 1

    data = resp.json()
    refresh_token = data.get("refresh_token")
    access_token = data.get("access_token")

    print()
    print("=" * 70)
    print("SUCCESS")
    print("=" * 70)

    if not refresh_token:
        print("[WARNING] No refresh_token was returned. This usually happens when")
        print("          the account already granted consent. Revoke access at")
        print("          https://myaccount.google.com/permissions and retry.")
        print()

    if refresh_token:
        print("YOUTUBE_OAUTH_REFRESH_TOKEN =")
        print(refresh_token)
        print()

    if access_token:
        print("(Short-lived access token, for reference only:)")
        print(access_token)
        print()

    print("Now set these GitHub secrets (all three must be from the SAME client):")
    print(f"  YOUTUBE_OAUTH_CLIENT_ID     = {args.client_id}")
    print("  YOUTUBE_OAUTH_CLIENT_SECRET = <the secret you passed>")
    print("  YOUTUBE_OAUTH_REFRESH_TOKEN = <the refresh token printed above>")
    print()
    print("Then re-run the workflow.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
