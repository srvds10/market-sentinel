"""
FastAPI application entry point.

Starts the Engine as a background asyncio task so the trading loop
and the API share the same event loop (and therefore the same AppState
in memory — no IPC needed).

Run with:
  uvicorn api.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(os.environ.get("SENTINEL_ENV_FILE", "/opt/market-sentinel/.env"))
except ImportError:
    pass

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from api.routes import config as config_router
from api.routes import logs as logs_router
from api.routes import status as status_router
from api.routes import trades as trades_router
from api.ws import broadcast_loop, ws_endpoint
from core.engine import Engine, load_config

_LOG_FILE = os.environ.get("SENTINEL_LOG_FILE", "logs/market-sentinel.log")


def _setup_logging() -> None:
    Path(_LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")

    console = logging.StreamHandler()
    console.setFormatter(fmt)

    # Rotating file: 5 MB per file, keep last 3 (≈15 MB total)
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(console)
    root.addHandler(file_handler)


_setup_logging()
logger = logging.getLogger(__name__)

_CONFIG_PATH = os.environ.get("SENTINEL_CONFIG", "config.yaml")


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = Engine(config_path=_CONFIG_PATH)

    # Attach shared objects to app.state so routes can access them
    app.state.engine_state = engine.state
    app.state.db = engine._db
    app.state.config = engine._cfg
    app.state.config_path = _CONFIG_PATH
    app.state.instrument_manager = engine._instrument_manager
    app.state.execution_engine = engine._execution_engine

    # Open DB before engine tasks run
    await engine._db.open()

    engine_task = asyncio.create_task(engine.run(), name="engine")
    broadcast_task = asyncio.create_task(
        broadcast_loop(engine.state.broadcast_queue), name="broadcast"
    )

    logger.info("Market Sentinel API ready")
    yield

    engine_task.cancel()
    broadcast_task.cancel()
    try:
        await asyncio.gather(engine_task, broadcast_task, return_exceptions=True)
    finally:
        await engine._db.close()
    logger.info("Market Sentinel API shutdown complete")


app = FastAPI(
    title="Market Sentinel",
    description="NSE options IV-accumulation trading daemon",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(status_router.router)
app.include_router(trades_router.router)
app.include_router(config_router.router)
app.include_router(logs_router.router)


@app.websocket("/ws/live")
async def live_ws(websocket: WebSocket):
    await ws_endpoint(websocket)


@app.get("/health")
async def health():
    return {"status": "ok"}
