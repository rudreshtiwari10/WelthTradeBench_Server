from __future__ import annotations

import os
from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth_utils import create_access_token, decode_token, hash_password, verify_password
from ..database import get_db
from ..schemas import Token, UserCreate, UserInDB

router = APIRouter(prefix="/api/users", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


def _admin_email() -> str:
    return os.getenv("ADMIN_EMAIL", "").strip().lower()


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserInDB:
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    db = get_db()
    try:
        user_doc = await db.users.find_one({"_id": ObjectId(payload["sub"])})
    except Exception:
        user_doc = None

    if not user_doc:
        raise HTTPException(status_code=401, detail="User not found")

    admin = _admin_email()
    is_admin = bool(admin and user_doc["email"].lower() == admin)
    approved = is_admin or user_doc.get("approved", False)

    return UserInDB(
        id=str(user_doc["_id"]),
        email=user_doc["email"],
        approved=approved,
        is_admin=is_admin,
    )


async def require_approved_user(
    user: UserInDB = Depends(get_current_user),
) -> UserInDB:
    if not user.approved:
        raise HTTPException(status_code=403, detail="Account pending admin approval")
    return user


@router.post("/register", response_model=Token, status_code=201)
async def register(body: UserCreate) -> Token:
    db = get_db()
    if await db.users.find_one({"email": body.email}):
        raise HTTPException(status_code=409, detail="Email already registered")

    admin = _admin_email()
    is_admin_user = bool(admin and body.email.lower() == admin)

    hashed = hash_password(body.password)
    result = await db.users.insert_one({
        "email": body.email,
        "password_hash": hashed,
        "approved": is_admin_user,   # admin is auto-approved, others wait
        "created_at": datetime.now(timezone.utc),
    })
    token = create_access_token(str(result.inserted_id), body.email)
    return Token(access_token=token, token_type="bearer")


@router.post("/token", response_model=Token)
async def login(body: UserCreate) -> Token:
    db = get_db()
    user = await db.users.find_one({"email": body.email})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(str(user["_id"]), body.email)
    return Token(access_token=token, token_type="bearer")


@router.get("/me", response_model=UserInDB)
async def me(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    return current_user
