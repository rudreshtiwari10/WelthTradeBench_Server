from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from ..database import get_db
from ..schemas import DrawingsDoc, UserInDB
from .auth import require_approved_user

router = APIRouter(prefix="/api/drawings", tags=["drawings"])


@router.get("")
async def get_drawings(
    key: str = Query(..., description="symbol:interval key, e.g. NIFTY:1D"),
    user: UserInDB = Depends(require_approved_user),
) -> dict:
    db = get_db()
    doc = await db.drawings.find_one({"user_id": user.id, "key": key})
    return {"key": key, "drawings": doc["drawings"] if doc else []}


@router.put("")
async def save_drawings(
    body: DrawingsDoc,
    user: UserInDB = Depends(require_approved_user),
) -> dict:
    db = get_db()
    await db.drawings.update_one(
        {"user_id": user.id, "key": body.key},
        {"$set": {
            "user_id": user.id,
            "key": body.key,
            "drawings": body.drawings,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return {"ok": True}
