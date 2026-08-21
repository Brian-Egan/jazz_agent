"""One-off Spotify OAuth authorization-code flow to obtain a refresh token.

Run once, interactively, from a machine with a browser. Reads
SPOTIFY_CLIENT_ID/SECRET/REDIRECT_URI from .env via config.py, opens the
Spotify consent screen, catches the redirect on a local HTTP server, and
prints the refresh token to paste into .env as SPOTIFY_REFRESH_TOKEN. Once
obtained it is reused forever (ARCHITECTURE.md's config never re-runs this).
"""

from __future__ import annotations

import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from jazz_agent.config import load_config

_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"  # noqa: S105 -- URL, not a secret
_SCOPES = (
    "playlist-modify-private playlist-modify-public playlist-read-private "
    "user-read-currently-playing user-read-recently-played"
)


class _CallbackHandler(BaseHTTPRequestHandler):
    authorization_code: str | None = None

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's naming convention
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [None])[0]
        error = query.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

        if error:
            self.wfile.write(f"Authorization failed: {error}\n".encode())
        else:
            _CallbackHandler.authorization_code = code
            self.wfile.write(b"Authorized. You can close this tab and return to the terminal.\n")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # keep the console clean; this is a one-off local script, not a service


def _wait_for_authorization_code(redirect_uri: str) -> str:
    parsed = urlparse(redirect_uri)
    server = HTTPServer((parsed.hostname or "127.0.0.1", parsed.port or 80), _CallbackHandler)
    server.handle_request()  # blocks for exactly one request, then returns
    if _CallbackHandler.authorization_code is None:
        print("No authorization code received.", file=sys.stderr)
        raise SystemExit(1)
    return _CallbackHandler.authorization_code


def main() -> int:
    config = load_config()
    if not config.spotify_client_id or not config.spotify_client_secret:
        print("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env first.")
        return 1

    authorize_url = f"{_AUTHORIZE_URL}?" + urlencode(
        {
            "client_id": config.spotify_client_id,
            "response_type": "code",
            "redirect_uri": config.spotify_redirect_uri,
            "scope": _SCOPES,
        }
    )
    print(f"Opening browser for authorization:\n{authorize_url}\n")
    webbrowser.open(authorize_url)

    code = _wait_for_authorization_code(config.spotify_redirect_uri)

    response = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.spotify_redirect_uri,
        },
        auth=(config.spotify_client_id, config.spotify_client_secret),
    )
    response.raise_for_status()
    refresh_token = response.json()["refresh_token"]

    print("\nAdd this to .env:\n")
    print(f"SPOTIFY_REFRESH_TOKEN={refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
