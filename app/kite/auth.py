"""Kite Connect OAuth: login-URL builder + request_token → access_token exchange."""
from __future__ import annotations

import hashlib

import httpx

from ..config import KITE_API_KEY, KITE_API_SECRET

LOGIN_URL = "https://kite.zerodha.com/connect/login"
SESSION_URL = "https://api.kite.trade/session/token"


def login_url() -> str:
    # Note: NO redirect_uri here — Kite uses the one registered on the dev app.
    return f"{LOGIN_URL}?api_key={KITE_API_KEY}&v=3"


async def exchange_token(request_token: str) -> str:
    """Exchange the Kite request_token for an access token. Returns the token string."""
    checksum = hashlib.sha256(
        f"{KITE_API_KEY}{request_token}{KITE_API_SECRET}".encode()
    ).hexdigest()
    data = {
        "api_key": KITE_API_KEY,
        "request_token": request_token,
        "checksum": checksum,
    }
    headers = {
        "X-Kite-Version": "3",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(SESSION_URL, data=data, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
    token = (payload.get("data") or {}).get("access_token")
    if not token:
        raise ValueError(f"No access_token in Kite response: {payload}")
    return token
