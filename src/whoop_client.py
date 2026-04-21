"""HTTP client for the Whoop v2 developer API."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx


API_BASE = "https://api.prod.whoop.com/developer"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
REFRESH_LEEWAY_SECONDS = 60


class WhoopClient:
    """Authenticated client for Whoop's v2 REST API.

    The caller supplies an OAuth client_id/client_secret and a path to a
    `token.json` file produced by the one-time `auth.py` flow. Refresh
    tokens rotate on each refresh; the new pair is persisted back to disk.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_path: Path,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_path = token_path
        self._token: dict[str, Any] = self._load_token()
        self._http = httpx.Client(base_url=API_BASE, timeout=30.0)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> WhoopClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ---- token handling --------------------------------------------------

    def _load_token(self) -> dict[str, Any]:
        if not self._token_path.exists():
            raise RuntimeError(
                f"No token at {self._token_path}. Run the auth flow first."
            )
        return json.loads(self._token_path.read_text())

    def _save_token(self) -> None:
        tmp = self._token_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._token, indent=2))
        tmp.replace(self._token_path)

    def _refresh_token(self) -> None:
        resp = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "offline",
                "refresh_token": self._token["refresh_token"],
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = {
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
            "expires_at": int(time.time()) + int(payload["expires_in"]),
            "scope": payload.get("scope", ""),
            "token_type": payload.get("token_type", "bearer"),
        }
        self._save_token()

    def _auth_header(self) -> dict[str, str]:
        if time.time() >= self._token.get("expires_at", 0) - REFRESH_LEEWAY_SECONDS:
            self._refresh_token()
        return {"Authorization": f"Bearer {self._token['access_token']}"}

    # ---- low-level request helpers --------------------------------------

    def _get(self, path: str, **params: Any) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v is not None}
        resp = self._http.get(path, headers=self._auth_header(), params=params)
        if resp.status_code == 401:
            self._refresh_token()
            resp = self._http.get(path, headers=self._auth_header(), params=params)
        resp.raise_for_status()
        return resp.json()

    def _paginated(self, path: str, **params: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        next_token: str | None = None
        while True:
            page_params = dict(params)
            if next_token:
                page_params["nextToken"] = next_token
            page = self._get(path, **page_params)
            records.extend(page.get("records", []))
            next_token = page.get("next_token")
            if not next_token:
                return records

    # ---- v2 endpoints ----------------------------------------------------

    def get_profile(self) -> dict[str, Any]:
        return self._get("/v2/user/profile/basic")

    def get_body_measurement(self) -> dict[str, Any]:
        return self._get("/v2/user/measurement/body")

    def get_cycle(self, cycle_id: str) -> dict[str, Any]:
        return self._get(f"/v2/cycle/{cycle_id}")

    def get_cycle_collection(
        self, start: str, end: str, limit: int = 25
    ) -> list[dict[str, Any]]:
        return self._paginated("/v2/cycle", start=start, end=end, limit=limit)

    def get_recovery_for_cycle(self, cycle_id: str) -> dict[str, Any]:
        return self._get(f"/v2/cycle/{cycle_id}/recovery")

    def get_sleep_for_cycle(self, cycle_id: str) -> dict[str, Any]:
        return self._get(f"/v2/cycle/{cycle_id}/sleep")

    def get_recovery_collection(
        self, start: str, end: str, limit: int = 25
    ) -> list[dict[str, Any]]:
        return self._paginated("/v2/recovery", start=start, end=end, limit=limit)

    def get_sleep(self, sleep_id: str) -> dict[str, Any]:
        return self._get(f"/v2/activity/sleep/{sleep_id}")

    def get_sleep_collection(
        self, start: str, end: str, limit: int = 25
    ) -> list[dict[str, Any]]:
        return self._paginated(
            "/v2/activity/sleep", start=start, end=end, limit=limit
        )

    def get_workout(self, workout_id: str) -> dict[str, Any]:
        return self._get(f"/v2/activity/workout/{workout_id}")

    def get_workout_collection(
        self, start: str, end: str, limit: int = 25
    ) -> list[dict[str, Any]]:
        return self._paginated(
            "/v2/activity/workout", start=start, end=end, limit=limit
        )
