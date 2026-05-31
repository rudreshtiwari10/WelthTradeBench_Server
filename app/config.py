"""Configuration + access-token store for the backend.

Reads Upstox credentials from server/.env. When credentials/token are absent
the app serves mock data, so everything is demoable without an Upstox account.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("UPSTOX_API_KEY", "").strip()
API_SECRET = os.getenv("UPSTOX_API_SECRET", "").strip()
REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8000/auth/callback").strip()
SANDBOX = os.getenv("UPSTOX_SANDBOX", "false").strip().lower() == "true"
PORT = int(os.getenv("PORT", "8000"))

# Where the client dev server lives (for post-login redirect).
CLIENT_URL = os.getenv("CLIENT_URL", "http://localhost:5173").strip()

_TOKEN_FILE = BASE_DIR / ".token.json"


class TokenStore:
    """Holds the daily Upstox access token. Persisted to disk so a server
    restart within the same day keeps the session."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._issued: float = 0.0
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(_TOKEN_FILE.read_text())
            self._token = data.get("access_token")
            self._issued = float(data.get("issued", 0))
        except Exception:
            self._token = None

    def set(self, token: str) -> None:
        self._token = token
        self._issued = time.time()
        try:
            _TOKEN_FILE.write_text(json.dumps({"access_token": token, "issued": self._issued}))
        except Exception:
            pass

    def clear(self) -> None:
        self._token = None
        try:
            _TOKEN_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    @property
    def token(self) -> str | None:
        # Upstox tokens expire at ~03:30 UTC daily; treat >18h as stale.
        if self._token and (time.time() - self._issued) > 18 * 3600:
            return None
        return self._token

    @property
    def authenticated(self) -> bool:
        return self.token is not None


def credentials_present() -> bool:
    return bool(API_KEY and API_SECRET)


tokens = TokenStore()
