"""Upstox OAuth: authorize-URL builder + code→token exchange."""
from __future__ import annotations

import urllib.parse

import httpx

from ..config import API_KEY, API_SECRET, REDIRECT_URI

AUTH_DIALOG = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def login_url(state: str = "tradomate") -> str:
    params = {
        "response_type": "code",
        "client_id": API_KEY,
        "redirect_uri": REDIRECT_URI,
        "state": state,
    }
    return f"{AUTH_DIALOG}?{urllib.parse.urlencode(params)}"


async def exchange_code(code: str) -> str:
    """Exchange the OAuth code for an access token. Returns the token string."""
    data = {
        "code": code,
        "client_id": API_KEY,
        "client_secret": API_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    headers = {
        "accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(TOKEN_URL, data=data, headers=headers)
        resp.raise_for_status()
        payload = resp.json()
    token = payload.get("access_token")
    if not token:
        raise ValueError(f"No access_token in Upstox response: {payload}")
    return token
