"""
WebSocket broadcaster.

One asyncio.Queue per connected browser client.
The engine pushes events to AppState.broadcast_queue;
this module fans them out to all active connections.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._clients.add(q)
        logger.debug("WS client connected (%d total)", len(self._clients))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)
        logger.debug("WS client disconnected (%d remaining)", len(self._clients))

    def broadcast(self, msg: dict) -> None:
        dead: set[asyncio.Queue] = set()
        for q in self._clients:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.add(q)
        for q in dead:
            self._clients.discard(q)


manager = ConnectionManager()


async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    q = manager.subscribe()
    try:
        while True:
            msg = await q.get()
            await websocket.send_text(json.dumps(msg, default=str))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        manager.unsubscribe(q)


async def broadcast_loop(broadcast_queue: asyncio.Queue) -> None:
    """Drains the engine's broadcast queue and fans out to all WS clients."""
    while True:
        msg = await broadcast_queue.get()
        manager.broadcast(msg)
