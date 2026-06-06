from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db
from ..schemas import LayoutDoc, UserInDB
from .auth import require_approved_user

router = APIRouter(prefix="/api/layouts", tags=["layouts"])


@router.get("")
async def get_layouts(user: UserInDB = Depends(require_approved_user)) -> list[dict]:
    db = get_db()
    docs = await db.layouts.find({"user_id": user.id}).to_list(length=1000)
    return [
        {
            "id": d["id"],
            "name": d["name"],
            "symbol": d["symbol"],
            "interval": d["interval"],
            "chartType": d["chartType"],
            "indicators": d.get("indicators", []),
            "gridLayout": d.get("gridLayout"),
            "panels": d.get("panels", []),
        }
        for d in docs
    ]


@router.post("", status_code=201)
async def create_layout(
    body: LayoutDoc,
    user: UserInDB = Depends(require_approved_user),
) -> dict:
    db = get_db()
    await db.layouts.update_one(
        {"user_id": user.id, "id": body.id},
        {"$set": {
            "user_id": user.id,
            "id": body.id,
            "name": body.name,
            "symbol": body.symbol,
            "interval": body.interval,
            "chartType": body.chartType,
            "indicators": body.indicators,
            "gridLayout": body.gridLayout,
            "panels": body.panels,
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return {"ok": True}


@router.put("/{layout_id}")
async def update_layout(
    layout_id: str,
    body: LayoutDoc,
    user: UserInDB = Depends(require_approved_user),
) -> dict:
    db = get_db()
    result = await db.layouts.update_one(
        {"user_id": user.id, "id": layout_id},
        {"$set": {
            "name": body.name,
            "symbol": body.symbol,
            "interval": body.interval,
            "chartType": body.chartType,
            "indicators": body.indicators,
            "gridLayout": body.gridLayout,
            "panels": body.panels,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Layout not found")
    return {"ok": True}


@router.delete("/{layout_id}")
async def delete_layout(
    layout_id: str,
    user: UserInDB = Depends(require_approved_user),
) -> dict:
    db = get_db()
    result = await db.layouts.delete_one({"user_id": user.id, "id": layout_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Layout not found")
    return {"ok": True}
