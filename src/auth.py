"""One-time OAuth 2.0 authorization-code flow for Whoop.

Run this once to produce `config/token.json`, which the MCP server
subsequently uses (and refreshes) to authenticate API calls.

Invocation:
    python src/auth.py

The script:
  1. Starts a short-lived HTTP listener on WHOOP_REDIRECT_URI (default
     http://localhost:8765/callback).
  2. Opens the Whoop authorization URL — user signs in and grants access.
  3. Receives the redirect, exchanges the code for an access + refresh
     token pair, and writes them to WHOOP_TOKEN_PATH.
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv


AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
DEFAULT_REDIRECT = "http://localhost:8765/callback"
SCOPES = [
    "offline",
    "read:profile",
    "read:body_measurement",
    "read:cycles",
    "read:recovery",
    "read:sleep",
    "read:workout",
]


def _load_env() -> tuple[str, str, str, Path]:
    env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)

    client_id = os.environ.get("WHOOP_CLIENT_ID", "").strip()
    client_secret = os.environ.get("WHOOP_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("WHOOP_REDIRECT_URI", DEFAULT_REDIRECT).strip()
    token_path = Path(
        os.environ.get(
            "WHOOP_TOKEN_PATH",
            str(Path(__file__).resolve().parent.parent / "config" / "token.json"),
        )
    )

    if not client_id or not client_secret:
        sys.exit("WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET must be set")

    return client_id, client_secret, redirect_uri, token_path


def _build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    received: dict[str, str] = {}
    expected_state: str = ""

    def do_GET(self) -> None:  # noqa: N802 — stdlib interface
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.rstrip("/") != urllib.parse.urlparse(
            _CallbackHandler.redirect_path
        ).path.rstrip("/"):
            self.send_error(404)
            return

        qs = dict(urllib.parse.parse_qsl(parsed.query))
        if qs.get("state") != _CallbackHandler.expected_state:
            self.send_error(400, "state mismatch")
            return

        _CallbackHandler.received = qs
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if "code" in qs:
            self.wfile.write(b"Authorization received. You can close this tab.")
        else:
            err = qs.get("error", "unknown")
            self.wfile.write(f"OAuth error: {err}".encode())

    def log_message(self, *_: object) -> None:
        pass  # silence default access logs


def _await_code(redirect_uri: str, state: str) -> str:
    parsed = urllib.parse.urlparse(redirect_uri)
    host = "0.0.0.0"  # bind all interfaces so podman port-forward reaches us
    port = parsed.port or 80

    _CallbackHandler.expected_state = state
    _CallbackHandler.redirect_path = parsed.path or "/"

    with http.server.HTTPServer((host, port), _CallbackHandler) as httpd:
        print(f"Listening for callback on {redirect_uri}", file=sys.stderr)
        while not _CallbackHandler.received:
            httpd.handle_request()

    if "code" not in _CallbackHandler.received:
        sys.exit(f"OAuth failed: {_CallbackHandler.received}")
    return _CallbackHandler.received["code"]


def _exchange_code(
    client_id: str, client_secret: str, redirect_uri: str, code: str
) -> dict[str, object]:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    client_id, client_secret, redirect_uri, token_path = _load_env()
    state = secrets.token_urlsafe(32)
    auth_url = _build_auth_url(client_id, redirect_uri, state)

    print("Open this URL in your browser if it doesn't launch:", file=sys.stderr)
    print(auth_url, file=sys.stderr)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    code = _await_code(redirect_uri, state)
    payload = _exchange_code(client_id, client_secret, redirect_uri, code)

    token = {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": int(time.time()) + int(payload["expires_in"]),
        "scope": payload.get("scope", ""),
        "token_type": payload.get("token_type", "bearer"),
    }

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(token, indent=2))
    token_path.chmod(0o600)
    print(f"Wrote {token_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
