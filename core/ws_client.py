"""
Tick provider: Dhan HQ live market data WebSocket.

Connects directly via the websockets library, parsing Dhan's binary ticker
protocol. This avoids dhanhq.MarketFeed.__init__ calling asyncio.set_event_loop()
which replaces uvicorn's running loop and causes code=1006 disconnects.

Binary ticker packet layout (16 bytes):
  offset 0   uint8   packet_type  (2 = Ticker, 6 = extended ticker)
  offset 1   uint8   exchange_segment
  offset 2   uint16  reserved
  offset 4   uint32  security_id  (little-endian)
  offset 8   float32 ltp          (little-endian)
  offset 12  uint32  timestamp    (Unix seconds, little-endian)
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

from core.signal import Tick

logger = logging.getLogger(__name__)


@dataclass
class DhanInstrument:
    security_id: str
    exchange_segment: str   # "NSE_IDX" | "NSE_FNO" | "NSE_EQ"
    symbol: str             # human-readable label used as Tick.symbol


class TickProvider(ABC):
    def __init__(self, queue: asyncio.Queue[Tick]) -> None:
        self._queue = queue
        self._running = False

    @abstractmethod
    async def run(self) -> None: ...

    def stop(self) -> None:
        self._running = False

    async def _emit(self, tick: Tick) -> None:
        await self._queue.put(tick)


# Exchange segment string → subscription string used in JSON payload
_SEGMENT_SUB: dict[str, str] = {
    "NSE_IDX":  "IDX_I",
    "NSE_EQ":   "NSE_EQ",
    "NSE_FNO":  "NSE_FNO",
    "NSE_CURR": "NSE_CURR",
    "BSE_EQ":   "BSE_EQ",
    "BSE_FNO":  "BSE_FNO",
}

# Packet type bytes that carry an LTP field at offset 8
_LTP_PACKET_TYPES = frozenset({2, 6})

# Dhan disconnect packet: first byte = 50 (0x32), reason code at bytes 8-9 (uint16 LE)
_DISCONNECT_REASONS: dict[int, str] = {
    805: "too many connections",
    806: "session limit exceeded",
    807: "access token expired",
    808: "invalid client ID",
    809: "authentication failed",
}


class DhanWSClient(TickProvider):
    """Live Nifty feed via Dhan HQ WebSocket.

    Uses the websockets library directly instead of dhanhq.MarketFeed to avoid
    the asyncio event-loop replacement bug. Reconnects automatically with
    exponential back-off.
    """

    _WS_URL = (
        "wss://api-feed.dhan.co"
        "?version=2&token={token}&clientId={client_id}&authType=2"
    )

    def __init__(
        self,
        queue: asyncio.Queue[Tick],
        client_id: str,
        access_token: str,
        instrument_provider: Callable[[], list[DhanInstrument]],
        reconnect_delay: float = 5.0,
    ) -> None:
        super().__init__(queue)
        self._client_id = client_id
        self._access_token = access_token
        self._instrument_provider = instrument_provider
        self._reconnect_delay = reconnect_delay

    def update_token(self, token: str) -> None:
        self._access_token = token

    async def run(self) -> None:
        self._running = True
        delay = self._reconnect_delay
        while self._running:
            try:
                await self._connect_and_stream()
                delay = self._reconnect_delay   # reset on clean session
            except asyncio.CancelledError:
                break
            except Exception as exc:
                exc_str = str(exc)
                is_429  = "429" in exc_str
                is_disc = "disconnect packet" in exc_str or "code=" in exc_str
                level   = logger.error if is_disc else logger.warning
                level("Dhan WS error: %s — reconnecting in %.0fs", exc, delay)
                if self._running:
                    await asyncio.sleep(delay)
                delay = min(delay * (3 if is_429 else 2), 120.0)
                continue
            if self._running:
                await asyncio.sleep(delay)

    async def _connect_and_stream(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets not installed — run: pip install websockets")
            await asyncio.sleep(30)
            return

        instruments = self._instrument_provider()
        if not instruments:
            logger.warning("No instruments yet — waiting for InstrumentManager")
            await asyncio.sleep(10)
            return

        inst_list = []
        for inst in instruments:
            seg = _SEGMENT_SUB.get(inst.exchange_segment)
            if seg is None:
                logger.warning("Unknown exchange segment '%s' for %s — skipping",
                               inst.exchange_segment, inst.symbol)
                continue
            inst_list.append({"ExchangeSegment": seg, "SecurityId": inst.security_id})

        if not inst_list:
            logger.error("No valid instruments after segment mapping")
            await asyncio.sleep(10)
            return

        sym_map = {inst.security_id: inst.symbol for inst in instruments}
        url = self._WS_URL.format(token=self._access_token, client_id=self._client_id)
        sub_msg = json.dumps({
            "RequestCode": 15,   # 15 = Subscribe Ticker
            "InstrumentCount": len(inst_list),
            "InstrumentList": inst_list,
        })

        logger.info("Dhan WS connecting — %d instruments: %s",
                    len(instruments), [i.symbol for i in instruments])

        async with websockets.connect(url, open_timeout=15) as ws:
            logger.info("Dhan WS connected")
            await ws.send(sub_msg)
            logger.info("Dhan WS subscribed to %d instruments", len(inst_list))

            while self._running:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                except asyncio.TimeoutError:
                    logger.debug("Dhan WS: no tick in 30 s — still connected")
                    continue
                except Exception as recv_exc:
                    code   = getattr(recv_exc, "code", None)
                    reason = getattr(recv_exc, "reason", None)
                    if code is not None or reason is not None:
                        hint = ""
                        if code == 1006:
                            hint = (
                                " — Server closed without explanation. "
                                "Check Dhan portal: (1) Market Feed subscription active, "
                                "(2) server IP whitelisted under My Apps → Allowed IPs."
                            )
                        raise ConnectionError(
                            f"Dhan WS closed — code={code} reason={reason!r}{hint}"
                        ) from recv_exc
                    raise

                if isinstance(raw, bytes):
                    if len(raw) >= 1 and raw[0] == 50:  # disconnect packet
                        reason_code = struct.unpack_from("<H", raw, 8)[0] if len(raw) >= 10 else 0
                        reason_str = _DISCONNECT_REASONS.get(reason_code, f"code={reason_code}")
                        raise ConnectionError(f"Dhan WS disconnect packet — {reason_str}")
                    tick = self._parse_binary(raw, sym_map)
                    if tick:
                        await self._emit(tick)
                # str messages are JSON ack/status frames — ignore

    @staticmethod
    def _parse_binary(data: bytes, sym_map: dict[str, str]) -> Tick | None:
        if len(data) < 12:
            return None
        packet_type = data[0]
        if packet_type not in _LTP_PACKET_TYPES:
            return None
        try:
            security_id = str(struct.unpack_from("<I", data, 4)[0])
            ltp = struct.unpack_from("<f", data, 8)[0]
        except struct.error:
            return None
        if not ltp or security_id not in sym_map:
            return None
        return Tick(
            symbol=sym_map[security_id],
            security_id=security_id,
            ltp=float(ltp),
            timestamp=time.time(),
        )
