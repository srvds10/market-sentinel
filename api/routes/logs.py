"""
Log file download and clear endpoints.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(prefix="/api")

LOG_PATH = os.environ.get("SENTINEL_LOG_FILE", "logs/market-sentinel.log")


def _log_path() -> str:
    return LOG_PATH


@router.get("/logs/download")
async def download_logs():
    path = _log_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(
        path=path,
        media_type="text/plain",
        filename="market-sentinel.log",
    )


@router.delete("/logs/clear")
async def clear_logs():
    path = _log_path()
    if not os.path.exists(path):
        return JSONResponse({"cleared": True, "message": "No log file existed"})
    try:
        with open(path, "w") as f:
            f.truncate(0)
        return JSONResponse({"cleared": True})
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/size")
async def log_size():
    path = _log_path()
    if not os.path.exists(path):
        return {"bytes": 0, "exists": False}
    size = os.path.getsize(path)
    return {"bytes": size, "exists": True}
