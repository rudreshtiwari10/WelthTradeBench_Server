from __future__ import annotations

import os

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from ..database import get_db
from ..schemas import UserInDB
from .auth import get_current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(user: UserInDB = Depends(get_current_user)) -> UserInDB:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/users")
async def list_users(admin: UserInDB = Depends(_require_admin)) -> list[dict]:
    db = get_db()
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    docs = await db.users.find({}).sort("created_at", 1).to_list(length=5000)
    return [
        {
            "id": str(d["_id"]),
            "email": d["email"],
            "approved": bool(admin_email and d["email"].lower() == admin_email) or d.get("approved", False),
            "is_admin": bool(admin_email and d["email"].lower() == admin_email),
            "created_at": d.get("created_at", "").isoformat() if d.get("created_at") else "",
        }
        for d in docs
    ]


@router.post("/users/{user_id}/approve")
async def approve_user(
    user_id: str,
    admin: UserInDB = Depends(_require_admin),
) -> dict:
    db = get_db()
    try:
        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"approved": True}},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}


@router.post("/users/{user_id}/reject")
async def reject_user(
    user_id: str,
    admin: UserInDB = Depends(_require_admin),
) -> dict:
    db = get_db()
    try:
        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"approved": False}},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}
