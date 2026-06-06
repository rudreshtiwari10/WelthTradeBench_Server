from __future__ import annotations

import os
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

_client: AsyncIOMotorClient | None = None


def get_db() -> AsyncIOMotorDatabase:
    if _client is None:
        raise RuntimeError("Database not connected — call connect_db() first")
    return _client["welthwest"]


async def connect_db() -> None:
    global _client
    url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    _client = AsyncIOMotorClient(url)
    db = _client["welthwest"]
    await db.users.create_index("email", unique=True)
    await db.layouts.create_index([("user_id", 1), ("id", 1)], unique=True)
    await db.drawings.create_index([("user_id", 1), ("key", 1)], unique=True)


async def close_db() -> None:
    global _client
    if _client:
        _client.close()
        _client = None
