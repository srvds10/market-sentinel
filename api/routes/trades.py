from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api")


@router.get("/trades")
async def get_trades(
    request: Request,
    limit: int = Query(default=50, le=500),
    open_only: bool = Query(default=False),
) -> list[dict]:
    db = request.app.state.db
    return await db.get_trades(limit=limit, open_only=open_only)


@router.get("/signals")
async def get_signals(
    request: Request,
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    db = request.app.state.db
    return await db.get_signals(limit=limit)
