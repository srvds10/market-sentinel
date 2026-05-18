"""
Records management — clear trades, signals, and/or system log.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api")


@router.delete("/records/clear")
async def clear_records(
    request: Request,
    trades: bool = Query(default=True,  description="Clear trades table"),
    signals: bool = Query(default=True,  description="Clear signal_events table"),
    logs: bool = Query(default=False, description="Also clear system_log table"),
) -> JSONResponse:
    """
    Delete records from the database.  By default clears trades + signals.
    Pass ?logs=true to also wipe the system log.
    Returns the number of rows deleted from each table.
    """
    db = request.app.state.db
    counts = await db.clear_records(trades=trades, signals=signals, logs=logs)
    return JSONResponse({"cleared": True, "deleted": counts})
