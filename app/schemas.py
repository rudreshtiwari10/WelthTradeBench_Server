from __future__ import annotations

from typing import Any
from pydantic import BaseModel


class UserCreate(BaseModel):
    email: str
    password: str


class UserInDB(BaseModel):
    id: str
    email: str
    approved: bool = False
    is_admin: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str


class LayoutDoc(BaseModel):
    id: str
    name: str
    symbol: dict[str, Any]
    interval: str
    chartType: str
    indicators: list[dict[str, Any]] = []
    gridLayout: str | None = None
    panels: list[dict[str, Any]] = []


class DrawingsDoc(BaseModel):
    key: str
    drawings: list[dict[str, Any]] = []
