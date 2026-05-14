"""
Tick provider: Dhan HQ live market data WebSocket.

Uses the official dhanhq MarketFeed (v2.1.0+ API):
  from dhanhq import DhanContext, MarketFeed

Exchange segment and subscription constants are class attributes:
  MarketFeed.IDX, MarketFeed.NSE_FNO, MarketFeed.Ticker etc.

run_forever() starts the feed in a background thread. We poll
get_data() via run_in_executor so the asyncio event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
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


class DhanWSClient(TickProvider):
    """Live Nifty feed via Dhan HQ WebSocket.

    Uses the documented dhanhq v2.1.0+ API:
        feed = MarketFeed(dhan_context, instruments, version)
        feed.run_forever()
        while True:
            data = feed.get_data()

    Reconnects automatically on disconnection.
    """

    # Exchange segment string → MarketFeed class constant name
    # Actual integers: IDX=0, NSE=1, NSE_FNO=2
    _SEGMENT_ATTR = {
        "NSE_IDX":  "IDX",
        "NSE_EQ":   "NSE",
        "NSE_FNO":  "NSE_FNO",
        "NSE_CURR": "NSE_CURR",
        "BSE_FNO":  "BSE_FNO",
    }

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

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Dhan WS error: %s — reconnecting in %ss",
                               exc, self._reconnect_delay)
            if self._running:
                await asyncio.sleep(self._reconnect_delay)

    async def _connect_and_stream(self) -> None:
        try:
            from dhanhq import DhanContext, MarketFeed
        except ImportError:
            logger.error("dhanhq not installed — run: pip install dhanhq")
            await asyncio.sleep(30)
            return

        instruments = self._instrument_provider()
        if not instruments:
            logger.warning("No instruments yet — waiting for InstrumentManager")
            await asyncio.sleep(10)
            return

        dhan_instruments = []
        for inst in instruments:
            attr = self._SEGMENT_ATTR.get(inst.exchange_segment)
            seg = getattr(MarketFeed, attr, None) if attr else None
            if seg is None:
                logger.warning("Unknown exchange segment '%s' for %s — skipping",
                               inst.exchange_segment, inst.symbol)
                continue
            dhan_instruments.append((seg, inst.security_id, MarketFeed.Ticker))

        if not dhan_instruments:
            logger.error("No valid instruments after segment mapping")
            await asyncio.sleep(10)
            return

        sym_map = {inst.security_id: inst.symbol for inst in instruments}

        logger.info("Dhan WS connecting — %d instruments: %s",
                    len(instruments), [i.symbol for i in instruments])

        context = DhanContext(self._client_id, self._access_token)
        feed = MarketFeed(context, dhan_instruments, "v2")

        loop = asyncio.get_running_loop()

        try:
            # run_forever() starts the WebSocket in a background thread (non-blocking)
            feed.run_forever()
            logger.info("Dhan WS connected")

            while self._running:
                # get_data() may block briefly — run in executor to keep event loop free
                data = await loop.run_in_executor(None, feed.get_data)
                if data:
                    tick = self._parse(data, sym_map)
                    if tick:
                        await self._emit(tick)
                else:
                    await asyncio.sleep(0.05)

        finally:
            try:
                feed.close_connection()
            except Exception:
                pass

    @staticmethod
    def _parse(data: dict, sym_map: dict[str, str]) -> Tick | None:
        ltp = data.get("LTP") or data.get("ltp")
        security_id = str(data.get("security_id", ""))
        if not ltp or not security_id:
            return None
        try:
            return Tick(
                symbol=sym_map.get(security_id, security_id),
                security_id=security_id,
                ltp=float(ltp),
                timestamp=time.time(),
            )
        except (ValueError, TypeError):
            return None
